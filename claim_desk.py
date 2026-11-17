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
    - Every agent answers in plain lines that people can read. The program prints each answer
      in a box, and the next agent gets exactly that text. Nothing is hidden.
    - The order of the steps is fixed in this file. That makes it a "workflow":
      the model never decides which agent comes next.

Run it:
    python claim_desk.py data/claims/claim_1_covered.txt           (real mode, needs OPENAI_API_KEY)
    python claim_desk.py data/claims/claim_1_covered.txt --demo    (demo mode: no key, no cost)
    python claim_desk.py my_email.eml                              (your own email, real mode)

Your own email can be a .txt, .eml, .msg (Outlook), .pdf, .docx or .html file.
For .msg, .pdf and .docx, install one extra package first (the program tells you which).
"""

import io
import json
import os
import re
import sys
import textwrap
from html import unescape
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
MAX_EMAIL_CHARS = 8000         # very long emails are cut, to protect the budget

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


def read_lines(text):
    """Read lines like 'Policy number: DH-1001' into labels and values, so plain code can check them."""
    found = {}
    for line in str(text).split("\n"):
        line = line.replace("**", "").strip().lstrip("-• ").strip()
        if ":" in line:
            label, value = line.split(":", 1)
            found[label.strip().lower()] = value.strip()
    return found


def is_unknown(value):
    """Empty, or 'unknown' and the like, counts as missing."""
    return value.strip().strip(".").lower() in ("", "unknown", "not stated", "not given", "none", "n/a", "null")


def show_box(title, text, goes_to=""):
    """Print a text in a box, with a title and where the text goes next."""
    width = 80
    print("┌" + "─" * width)
    print("│ " + title)
    if goes_to:
        print("│ → " + goes_to)
    print("├" + "─" * width)
    for line in str(text).strip().split("\n"):
        for part in (textwrap.wrap(line, width - 2) or [""]):
            print("│ " + part)
    print("└" + "─" * width)


# ----------------------------------------------------------------------------
# Reading an email file: turn .txt, .eml, .msg, .pdf, .docx or .html into plain text
# ----------------------------------------------------------------------------

def need(package, module):
    """Load an extra package that a file type needs, or say how to install it."""
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError:
        sys.exit(f"To read this file type, first install a package:  pip install {package}")


def html_to_text(page):
    """Remove the layout code from an HTML email and keep the text."""
    page = re.sub(r"(?is)<(head|script|style)\b.*?</\1>", "", page)
    page = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", page)
    text = unescape(re.sub(r"<[^>]+>", "", page))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def with_header(sender, subject, body):
    """Put the sender and the subject (if known) above the text of the email."""
    header = [label + ": " + str(value) for label, value in (("From", sender), ("Subject", subject)) if value]
    return "\n".join(header + [""] + [body.strip()]).strip()


def read_email_file(path):
    """Turn an email file into plain text."""
    path = Path(path)
    data = path.read_bytes()
    kind = path.suffix.lower().lstrip(".")
    if kind in ("txt", "md", "text", ""):
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                pass
    if kind in ("html", "htm"):
        return html_to_text(data.decode("utf-8", errors="replace"))
    if kind == "eml":
        from email import policy
        from email.parser import BytesParser
        message = BytesParser(policy=policy.default).parsebytes(data)
        part = message.get_body(preferencelist=("plain", "html"))
        body = "" if part is None else part.get_content()
        if part is not None and part.get_content_type() == "text/html":
            body = html_to_text(body)
        return with_header(message["from"], message["subject"], body)
    if kind == "msg":
        extract_msg = need("extract-msg", "extract_msg")
        message = extract_msg.openMsg(str(path))
        body = message.body or html_to_text((message.htmlBody or b"").decode("utf-8", errors="replace"))
        text = with_header(message.sender, message.subject, body)
        message.close()
        return text
    if kind == "pdf":
        pypdf = need("pypdf", "pypdf")
        return "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(io.BytesIO(data)).pages).strip()
    if kind == "docx":
        docx = need("python-docx", "docx")
        return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs).strip()
    sys.exit(f"Cannot read '{path.name}'. Use a .txt, .eml, .msg, .pdf, .docx or .html file.")


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
    """Return the facts as six plain lines, for example 'Policy number: DH-1001'."""
    if client is None:                                                   # demo mode
        return DEMO_ANSWERS[claim_name]["intake"]
    response = call_model(client, budget, "intake agent", read_prompt("intake"),
                          [{"role": "user", "content": email_text}])
    return response.output_text


# ============================================================================
# 6. CHECK: plain code, no AI. A check between the steps breaks the chain of mistakes.
# ============================================================================

def check_facts(facts):
    """Read agent 1's lines and return a list of problems. An empty list means: go on."""
    found = read_lines(facts)
    problems = []
    for label in ["customer name", "policy number", "date of damage", "estimated cost"]:
        if is_unknown(found.get(label, "")):
            problems.append(f"missing: {label}")
    return problems


# ============================================================================
# 7. AGENT 2 - POLICY: uses the tool, then decides on the cover
# ============================================================================

def policy_agent(client, budget, claim_name, facts):
    """Get agent 1's lines, use the tool, and return the decision as three plain lines."""
    if client is None:                                                   # demo mode
        policy_number = read_lines(facts)["policy number"]
        policy = lookup_policy(policy_number)                            # the real tool still runs
        print(f"   (demo) the policy agent asks for the tool: lookup_policy({policy_number})")
        print(f"   (demo) the tool returns: {policy.get('product', policy)}")
        return DEMO_ANSWERS[claim_name]["policy"]

    input_items = [{"role": "user", "content": facts}]                  # agent 1's output, unchanged
    for _ in range(3):                                   # at most 3 rounds of tool use
        response = call_model(client, budget, "policy agent", read_prompt("policy"), input_items,
                              tools=[LOOKUP_POLICY_TOOL])
        input_items += response.output                   # keep what the model said (and asked for)
        tool_calls = [item for item in response.output if item.type == "function_call"]
        if not tool_calls:                               # no tool needed any more: this is the decision
            return response.output_text
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

def letter_input(facts, decision):
    """The glue puts the outputs of agent 1 and agent 2 together. This is exactly what agent 3 gets."""
    return ("FACTS (output of agent 1, the intake agent):\n" + facts.strip() +
            "\n\nDECISION (output of agent 2, the policy agent):\n" + decision.strip())


def letter_agent(client, budget, claim_name, facts, decision):
    if client is None:                                                   # demo mode
        return DEMO_ANSWERS[claim_name]["letter"]
    response = call_model(client, budget, "letter agent", read_prompt("letter"),
                          [{"role": "user", "content": letter_input(facts, decision)}])
    return response.output_text


# ============================================================================
# 9. THE PERSON: nothing is sent. We write a draft for a claims handler to approve.
# ============================================================================

def report(claim_name, demo, facts, problems, decision, letter, budget, stopped_because=None):
    lines = [f"# Claim desk: {claim_name}", "",
             f"Mode: {'demo (fixed answers, no cost)' if demo else 'real (model ' + MODEL + ')'}", "",
             "## 1. Intake agent: the facts", "", "```text", facts.strip() or "(none)", "```", ""]
    if problems:
        lines += ["## 2. Check: STOPPED", "", "The chain stops here. A person must ask the customer for:", ""]
        lines += [f"- {p}" for p in problems] + [""]
    if decision:
        lines += ["## 2. Check: OK", "", "## 3. Policy agent: the decision", "", "```text",
                  decision.strip(), "```", ""]
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
    if demo and claim_name not in DEMO_ANSWERS:
        sys.exit("Demo mode only has fixed answers for the three emails in data/claims/. "
                 "Run your own email in real mode (without --demo).")

    client = connect(demo)
    budget = Budget()
    facts, problems, decision, letter = "", [], None, None

    try:
        email_text = read_email_file(claim_path)[:MAX_EMAIL_CHARS]
        show_box(f"TRIGGER: a claim email arrives ({claim_name})", email_text, "goes, unchanged, to agent 1 (intake)")

        print("AGENT 1  the intake agent reads the email ...")
        facts = intake_agent(client, budget, claim_name, email_text)
        show_box("AGENT 1 (intake): output", facts, "goes, unchanged, to the check and to agent 2 (policy)")

        print("CHECK    are the facts complete?")
        problems = check_facts(facts)
        if problems:
            print("         no: " + "; ".join(problems) + ". The chain stops. A person takes over.")
        else:
            print("         yes: agent 1's output goes on to agent 2")
            print("AGENT 2  the policy agent decides on the cover ...")
            decision = policy_agent(client, budget, claim_name, facts)
            show_box("AGENT 2 (policy): output", decision, "goes to agent 3 (letter), together with agent 1's output")

            show_box("AGENT 3 (letter): input = the outputs of agent 1 and agent 2", letter_input(facts, decision))
            print("AGENT 3  the letter agent writes the reply ...")
            letter = letter_agent(client, budget, claim_name, facts, decision)
            show_box("AGENT 3 (letter): output", letter, "goes to a person, who approves it")

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
