# Multi-agent claim desk

A small, working multi-agent system for teaching. Three AI agents and one person handle a made-up hail-damage claim at a fictional insurer.

Made for the course **Applied AI for Business** (University of Amsterdam, 2026-27). All people, policies and claims in this repository are invented.

```
TRIGGER    a claim email arrives
   ↓
AGENT 1    intake: reads the email and lists the facts
   ↓
CHECK      plain code: are the facts complete?   → if not: stop, a person takes over
   ↓
AGENT 2    policy: looks up the policy (a TOOL) and decides on the cover
   ↓
AGENT 3    letter: writes the reply to the customer
   ↓
PERSON     nothing is sent: a claims handler reads the draft and approves it
```

## What is what

| Part | What it is | Where |
|---|---|---|
| **Agent** | Instructions (a prompt) + a model + sometimes tools | `prompts/intake.md`, `prompts/policy.md`, `prompts/letter.md` |
| **Model** | The AI that writes each answer, reached through the OpenAI API | `MODEL` in `claim_desk.py` |
| **Tool** | A normal Python function the policy agent may ask us to run | `lookup_policy()` in `claim_desk.py` |
| **Glue** | The code that passes the work from one agent to the next | `main()` in `claim_desk.py` |
| **Hand-off** | Each agent answers in plain lines. The program prints them in a box, and the next agent gets exactly that text | `show_box()` in `claim_desk.py` |
| **Check** | Plain code between the steps, no AI | `check_facts()` in `claim_desk.py` |
| **Trigger** | What starts a run: here, you choose a claim email | `data/claims/` |
| **Sample emails** | Invented claim emails as .eml, .docx, .pdf and .html files, to try your own upload | `data/sample_emails/` |
| **Person** | The draft goes to a person; nothing is sent | `report()` in `claim_desk.py` |
| **Budget guard** | Stops a run before it costs too much | `Budget` in `claim_desk.py` |
| **Notebook** | The same system, step by step, for Google Colab | `claim_desk_colab.ipynb` |

The order of the steps is fixed in the code. That makes this a **workflow**: the model never decides which agent comes next. In an **agent** in the strict sense, a model would decide that itself.

Every part of `claim_desk.py` is labelled with comments. Start reading at section 10, "The glue".

## Try it without a key (demo mode, free; script and GitHub only)

Demo mode uses fixed example answers instead of the model. It shows the whole flow and costs nothing.

- **On GitHub:** Actions tab → *Run the claim desk* → *Run workflow* → tick *Demo mode*.
- **On your computer:** `python claim_desk.py data/claims/claim_1_covered.txt --demo`

## Run it for real on GitHub

1. **Copy this repository** to your own GitHub account: *Fork*, or download the files and upload them to a new repository. On a fork, open the Actions tab once and enable workflows.
2. **Make an OpenAI key with a spending limit.** In the OpenAI platform, create a separate project for this demo, set a monthly spend limit (a hard limit, for example $5) for that project, and create an API key in that project.
3. **Store the key as a secret.** In your repository: *Settings* → *Secrets and variables* → *Actions* → *New repository secret*. Name: `OPENAI_API_KEY`. Value: your key.
4. **Run it.** Actions tab → *Run the claim desk* → *Run workflow* → choose a claim. The result appears on the run's page, and as files you can download: the report and the draft letter as a PDF.

Never put your key in the code. Anyone can read a public repository; nobody can read its secrets.

## Run it in Google Colab (for students)

The notebook `claim_desk_colab.ipynb` runs the same claim desk step by step, with a plain-language explanation above every block. It is written for people who do not program, and it works on its own: the emails, policies and instructions are inside the notebook.

**Open the notebook in Colab:** https://colab.research.google.com/github/jorisdemmers/multi-agent-claim-desk/blob/main/claim_desk_colab.ipynb

If you copied this repository to your own account, replace `jorisdemmers/multi-agent-claim-desk` in that link with your account and repository name.

The notebook needs an OpenAI key: each student (or the class) uses a key stored as a Colab secret named `OPENAI_API_KEY` (the key icon in the left bar). Changes in the notebook do not change the files in this repository, and the other way round.

In the notebook, every agent prints its output in a box, with where it goes next. The trigger asks for a claim email: students paste it, or upload a file (.txt, .eml, .msg, .pdf, .docx or .html). Sample emails are in `data/sample_emails/` and `data/claims/`. Use invented emails only: the text is sent to OpenAI. When you approve the letter (type *yes*), it is saved as a PDF and your browser downloads it. The notebook has no demo mode; demo mode (below) is only for `claim_desk.py` and the GitHub workflow.

## Run it on your own computer

```
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."            # Mac or Linux
$env:OPENAI_API_KEY = "sk-..."            # Windows PowerShell
python claim_desk.py data/claims/claim_1_covered.txt
python claim_desk.py data/sample_emails/sample_1_burst_pipe.eml     # an email file
```

Your own email can be a .txt, .eml, .msg (Outlook), .pdf, .docx or .html file. For .msg, .pdf and .docx you need one extra package: `pip install extract-msg`, `pip install pypdf` or `pip install python-docx`. The program tells you which one if it is missing.

## Costs and limits

There are three layers of protection:

1. **At OpenAI (the real safeguard):** a separate project with a hard monthly spend limit. When the limit is reached, OpenAI blocks the requests (error 429). OpenAI notes that the limit is not instant, so spending can go slightly over.
2. **In the code:** a small, cheap model (`gpt-6-luna`: $0.10 per million input tokens and $0.50 per million output tokens, November 2026), at most 2,000 output tokens per answer, at most 6 calls per run, and a stop at $0.05 per run.
3. **On GitHub:** manual runs only, one run at a time, and a 5-minute time limit.

A normal run makes 4 calls to the model and should cost well under 1 cent. The cost of every run is shown at the bottom of its report.

## Safety

- The key lives only in a secret. GitHub does not give secrets to workflows started from someone else's copy (a pull request from a fork).
- Only people with write access to the repository can start a run (GitHub requires write access for manual runs).
- Use invented data only. Never put real customer data in a demo: what you send to an AI provider leaves your hands.

## Try this

1. Run claim 3. Why does it stop? Add the missing facts to the email and run it again.
2. Follow the boxes. Each agent's output is the next agent's input. Where would a mistake by agent 1 show up later?
3. Change one sentence in `prompts/letter.md`, for example: write the letter in Dutch. What changes?
4. Make the check stricter: stop when the estimated cost is above EUR 5,000, so a person must decide.
5. Add a fourth agent between the check and the policy agent: a fraud agent that flags claims that look odd.
6. Run the same claim five times. Is the decision the same every time? (One right answer is not the same as a right answer every time.)
7. Advanced: let a coordinator agent decide which agent goes next. Now it is an agent, not a workflow. What can go wrong?
8. Upload an email of your own (Colab, real mode), or run `python claim_desk.py my_email.eml`. Try a clear email, a vague one, and one with a policy number that is not in the database.

## About

Teaching material for Applied AI for Business, Amsterdam Business School, University of Amsterdam. The insurer, its policies and its customers are fictional. This repository is not connected to any real insurer.
