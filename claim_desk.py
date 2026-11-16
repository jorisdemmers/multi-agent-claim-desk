"""
MULTI-AGENT CLAIM DESK
A teaching example for the course Applied AI for Business (University of Amsterdam).

Three AI agents and one person handle a made-up hail-damage claim:

    TRIGGER   a claim email arrives            (here: you choose a file in data/claims/)
    AGENT 1   intake: lists the facts          (prompts/intake.md)
    CHECK     code: are the facts complete?    (if not, the chain stops here)
    AGENT 2   policy: decides on the cover     (prompts/policy.md, uses the TOOL lookup_policy)
    AGENT 3   letter: writes the reply         (prompts/letter.md)
    PERSON    nothing is sent: a claims handler reads the draft and approves it

What is what:
    - An AGENT is: instructions (a prompt) + a model + sometimes tools.
    - The GLUE is this Python file. It passes the work from one agent to the next.
    - The order of the steps is fixed in this file. That makes it a "workflow":
      the model never decides which agent comes next.

Run it:
    python claim_desk.py data/claims/claim_1_covered.txt           (real mode, needs OPENAI_API_KEY)
    python claim_desk.py data/claims/claim_1_covered.txt --demo    (demo mode: no key, no cost)
"""

import json
import os
import sys
from pathlib import Path

# ============================================================================
# 1. SETTINGS: which model, what it costs, and the budget for one run
# ============================================================================

MODEL = os.getenv("MODEL", "gpt-6-luna")   # a small, cheap OpenAI model (November 2026)

# Price in US dollars per 1 million tokens (OpenAI's model page, November 2026).
# A token is about 3/4 of an English word. Change these if you change the model.
PRICE_INPUT_PER_MILLION = 0.10
PRICE_OUTPUT_PER_MILLION = 0.50

# The budget for ONE run. The program stops when it would go over.
MAX_CALLS_PER_RUN = 6          # one run normally needs 4 calls to the model
MAX_COST_PER_RUN_USD = 0.05    # one run normally costs well under 1 cent
MAX_OUTPUT_TOKENS = 2000       # the most one answer may use (this includes the model's thinking)

HERE = Path(__file__).parent
DEMO_ANSWERS = json.loads((HERE / "demo" / "answers.json").read_text())


# ============================================================================
# 2. THE BUDGET GUARD: counts calls and cost, and stops the run if needed
# ============================================================================

class BudgetStop(Exception):
    """Raised when a run would go over its budget."""


class Budget:
    def __init__(self):
        self.calls = 0
        self.cost_usd = 0.0
        self.log = []                       # one line per call, for the report

    def before_call(self, agent):
        if self.calls >= MAX_CALLS_PER_RUN:
            raise BudgetStop(f"Stopped before {agent}: already {self.calls} calls (limit {MAX_CALLS_PER_RUN}).")

    def after_call(self, agent, input_tokens, output_tokens):
        cost = (input_tokens * PRICE_INPUT_PER_MILLION + output_tokens * PRICE_OUTPUT_PER_MILLION) / 1_000_000
        self.calls += 1
        self.cost_usd += cost
        self.log.append((agent, input_tokens, output_tokens, cost))
        if self.cost_usd > MAX_COST_PER_RUN_USD:
            raise BudgetStop(f"Stopped after {agent}: cost ${self.cost_usd:.4f} is over the limit "
                             f"${MAX_COST_PER_RUN_USD}.")


# ============================================================================
# 3. TALKING TO THE MODEL: one call = an agent's instructions + some input
# ============================================================================

def connect(demo):
    """Return an OpenAI client, or None in demo mode."""
    if demo:
        return None
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit("No OPENAI_API_KEY found. Add it as a secret (see README), or run with --demo.")
    from openai import OpenAI               # imported here, so demo mode works without the package
    return OpenAI()                          # the client reads the key from OPENAI_API_KEY itself


def call_model(client, budget, agent, instructions, input_items, tools=None):
    """Send one request to the model and return its response."""
    budget.before_call(agent)
    response = client.responses.create(
        model=MODEL,
        instructions=instructions,           # the agent's prompt: who it is and what it must do
        input=input_items,                   # the work it gets: an email, facts, a decision
        tools=tools or [],                   # what it may use (only the policy agent has a tool)
        max_output_tokens=MAX_OUTPUT_TOKENS,
        reasoning={"effort": "low"},         # less thinking = fewer tokens = cheaper
    )
    budget.after_call(agent, response.usage.input_tokens, response.usage.output_tokens)
    return response


def read_prompt(name):
    """An agent's instructions live in a plain text file, so you can read and change them."""
    return (HERE / "prompts" / f"{name}.md").read_text()


def parse_json(text):
    """Models are asked to answer in JSON. This reads it, and fails loudly if it is not JSON."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"The answer is not JSON: {text[:200]}")
    return json.loads(text[start:end + 1])


# ============================================================================
# 4. THE TOOL: a normal Python function that the policy agent may ask us to run
# ============================================================================

def lookup_policy(policy_number):
    """Look up a policy in the (invented) policy database."""
    policies = json.loads((HERE / "data" / "policies.json").read_text())
    return policies.get(policy_number, {"error": f"No policy found with number {policy_number}."})


# This description tells the model that the tool exists and what to send to it.
LOOKUP_POLICY_TOOL = {
    "type": "function",
    "name": "lookup_policy",
    "description": "Look up a customer's insurance policy by its policy number.",
    "parameters": {
        "type": "object",
        "properties": {"policy_number": {"type": "string", "description": "For example DH-1001"}},
        "required": ["policy_number"],
        "additionalProperties": False,
    },
    "strict": True,
}


# ============================================================================
# 5. AGENT 1 - INTAKE: reads the email and lists the facts
# ============================================================================

def intake_agent(client, budget, claim_name, email_text):
    if client is None:                                                   # demo mode
        return parse_json(DEMO_ANSWERS[claim_name]["intake"])
    response = call_model(client, budget, "intake agent", read_prompt("intake"),
                          [{"role": "user", "content": email_text}])
    return parse_json(response.output_text)


# ============================================================================
# 6. CHECK: plain code, no AI. A check between the steps breaks the chain of mistakes.
# ============================================================================

def check_facts(facts):
    """Return a list of problems. An empty list means: go on."""
    problems = []
    for key in ["customer_name", "policy_number", "date_of_damage", "estimated_cost_eur"]:
        if not facts.get(key):
            problems.append(f"missing: {key}")
    return problems


# ============================================================================
# 7. AGENT 2 - POLICY: uses the tool, then decides on the cover
# ============================================================================

def policy_agent(client, budget, claim_name, facts):
    if client is None:                                                   # demo mode
        policy = lookup_policy(facts["policy_number"])                   # the real tool still runs
        print(f"   (demo) the policy agent asks for the tool: lookup_policy({facts['policy_number']})")
        print(f"   (demo) the tool returns: {policy['product']}")
        return parse_json(DEMO_ANSWERS[claim_name]["policy"])

    input_items = [{"role": "user", "content": json.dumps(facts)}]
    for _ in range(3):                                   # at most 3 rounds of tool use
        response = call_model(client, budget, "policy agent", read_prompt("policy"), input_items,
                              tools=[LOOKUP_POLICY_TOOL])
        input_items += response.output                   # keep what the model said (and asked for)
        tool_calls = [item for item in response.output if item.type == "function_call"]
        if not tool_calls:                               # no tool needed any more: this is the decision
            return parse_json(response.output_text)
        for call in tool_calls:                          # the MODEL asks; OUR CODE runs the tool
            arguments = json.loads(call.arguments)
            print(f"   the policy agent asks for the tool: lookup_policy({arguments['policy_number']})")
            result = lookup_policy(**arguments)
            input_items.append({"type": "function_call_output", "call_id": call.call_id,
                                "output": json.dumps(result)})
    raise RuntimeError("The policy agent did not reach a decision after 3 rounds.")


# ============================================================================
# 8. AGENT 3 - LETTER: writes the reply to the customer
# ============================================================================

def letter_agent(client, budget, claim_name, facts, decision):
    if client is None:                                                   # demo mode
        return DEMO_ANSWERS[claim_name]["letter"]
    work = json.dumps({"facts": facts, "decision": decision})
    response = call_model(client, budget, "letter agent", read_prompt("letter"),
                          [{"role": "user", "content": work}])
    return response.output_text


# ============================================================================
# 9. THE PERSON: nothing is sent. We write a draft for a claims handler to approve.
# ============================================================================

def report(claim_name, demo, facts, problems, decision, letter, budget, stopped_because=None):
    lines = [f"# Claim desk: {claim_name}", "",
             f"Mode: {'demo (fixed answers, no cost)' if demo else 'real (model ' + MODEL + ')'}", "",
             "## 1. Intake agent: the facts", "", "```json", json.dumps(facts, indent=2), "```", ""]
    if problems:
        lines += ["## 2. Check: STOPPED", "", "The chain stops here. A person must ask the customer for:", ""]
        lines += [f"- {p}" for p in problems] + [""]
    if decision:
        lines += ["## 2. Check: OK", "", "## 3. Policy agent: the decision", "", "```json",
                  json.dumps(decision, indent=2), "```", ""]
    if letter:
        lines += ["## 4. Letter agent: the draft reply", "", "```text", letter, "```", ""]
    if stopped_because:
        lines += ["## Stopped by the budget guard", "", stopped_because, ""]
    lines += ["## 5. A person decides", "",
              "Nothing has been sent. A claims handler reads this draft and approves, changes or rejects it.", "",
              "Approved by: ____________________", "",
              "## Cost of this run", "",
              "| Agent | Input tokens | Output tokens | Cost (USD) |", "|---|---|---|---|"]
    lines += [f"| {a} | {i} | {o} | {c:.5f} |" for a, i, o, c in budget.log]
    lines += [f"| **Total ({budget.calls} calls)** | | | **{budget.cost_usd:.5f}** |", ""]
    text = "\n".join(lines)

    out_dir = HERE / "output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{Path(claim_name).stem}_draft.md").write_text(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):                 # on GitHub: also show it on the run's page
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text)
    return text


# ============================================================================
# 10. THE GLUE: the trigger, then the agents, one after the other
# ============================================================================

def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python claim_desk.py data/claims/claim_1_covered.txt [--demo]")
    claim_path = Path(sys.argv[1])
    demo = "--demo" in sys.argv
    claim_name = claim_path.name

    client = connect(demo)
    budget = Budget()
    facts, problems, decision, letter = {}, [], None, None

    try:
        print(f"TRIGGER  a claim email arrives: {claim_name}")
        email_text = claim_path.read_text()

        print("AGENT 1  intake agent reads the email ...")
        facts = intake_agent(client, budget, claim_name, email_text)

        print("CHECK    are the facts complete?")
        problems = check_facts(facts)
        if problems:
            print("         no: " + "; ".join(problems) + ". The chain stops. A person takes over.")
        else:
            print("         yes")
            print("AGENT 2  policy agent decides on the cover ...")
            decision = policy_agent(client, budget, claim_name, facts)
            print(f"         decision: {decision.get('decision')}")

            print("AGENT 3  letter agent writes the reply ...")
            letter = letter_agent(client, budget, claim_name, facts, decision)

        report(claim_name, demo, facts, problems, decision, letter, budget)

    except BudgetStop as stop:
        print(f"BUDGET   {stop}")
        report(claim_name, demo, facts, problems, decision, letter, budget, stopped_because=str(stop))
        sys.exit(1)
    except Exception as error:                           # e.g. the monthly spending limit is reached
        name = type(error).__name__
        print(f"ERROR    {name}: {error}")
        if name in ("RateLimitError", "BadRequestError", "PermissionDeniedError"):
            print("         This can mean the project's monthly spending limit is reached. See README, 'Costs'.")
        sys.exit(1)

    print(f"PERSON   draft written to output/{claim_path.stem}_draft.md. Nothing has been sent.")
    print(f"COST     {budget.calls} calls, ${budget.cost_usd:.5f}")


if __name__ == "__main__":
    main()
