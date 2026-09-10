# OpsPilot coding-agent context

## Product and architecture

- OpsPilot is an e-commerce after-sales and ticket collaboration Agent, not a generic chatbot.
- Python owns model access, tool schemas, RAG, LangGraph orchestration, checkpoints, and FastAPI endpoints.
- Java Spring Boot owns deterministic order rules and business writes such as return applications.
- The LLM may propose a tool call. Application code validates arguments and performs the operation.
- Do not duplicate Java return rules in prompts or Python conditionals.

## Teaching and collaboration contract

- Use Chinese for teaching, review, progress reports, and project explanations unless the user explicitly asks for another language.
- Treat the user as a Java-backend student moving toward Agent and LLM application engineering, not toward an algorithm-research role. Python syntax should be taught in the context of real FastAPI, RAG, tool-calling, LangGraph, evaluation, and engineering work.
- Teach by building the two portfolio projects. Do not finish every core feature for the student: create safe scaffolding, contracts, examples, and tests; let the student attempt the key logic; then perform code review and verification.
- If the user already understands a concept and only makes a spelling, class-name, or minor syntax error, correct it briefly and move on. Do not spend a full lesson repeatedly testing the same simple point.
- Before asking the user to use a new function, class, endpoint, or framework concept, explain what it is, what inputs and outputs it has, where it runs, and why the project needs it.
- Each lesson should cover one coherent, useful milestone and include: the lesson objective, a short principle, relevant runnable code, one meaningful exercise, and review criteria. Keep steps small enough to learn, but do not drip-feed one or two sentences at a time.
- When showing teaching code, explain the important lines with Chinese comments or an adjacent line-by-line walkthrough. Do not add noisy tutorial comments to production code when a separate explanation is clearer.
- When Codex writes or changes code, report what changed, how it works, why the design was chosen, which files matter, what was tested, and what limitations or next steps remain.
- If the user says “不会”, reduce the problem into smaller prompts and clues. Give the complete solution only after an attempt or when the user explicitly asks for the answer.
- Use failures as debugging lessons: start from the bottom of the Traceback, identify the exception type and first relevant project line, form a hypothesis, run a focused check, and only then change code.
- Run focused tests for every code change and the relevant regression suite for milestones. Never describe mock-only behavior as proof of real-model quality.
- Do not invent progress percentages. Report progress against named lesson or milestone checklist items and distinguish today’s lesson progress from the whole project and job-readiness progress.
- Read `LEARNING_PROGRESS.md` before continuing a lesson. Update it after a material teaching or project milestone so the next task resumes from the actual checkpoint.

## Internship outcome and project quality

- Target: be ready to start applying by 2026-09-26 for Agent development and LLM application development internships.
- Deliver two explainable portfolio projects, but prioritize a resume-ready, end-to-end OpsPilot vertical slice before expanding the second project.
- OpsPilot must demonstrate more than a chat API: grounded RAG with citations and evaluation, tool selection and multi-step execution, explicit confirmation for side effects, state/checkpoint recovery, retries and idempotency, FastAPI APIs, Java business services, PostgreSQL/pgvector, Redis, Docker, automated tests, documentation, and a demo path.
- Do not add multi-agent architecture or frameworks only for appearance. Every dependency and abstraction must solve a stated reliability, maintainability, or evaluation problem.
- The second project must represent a different realistic business workflow and add complementary evidence rather than duplicating OpsPilot under another name. Select its exact scope only after the first project has a demonstrable vertical slice.
- Resume bullets and interview claims must be supported by code, reproducible tests, evaluation data, or a working deployment. Never fabricate traffic, accuracy, latency, users, or production impact.
- Train the user to explain the design independently: problem, architecture, request flow, failure modes, tradeoffs, evaluation method, and one concrete debugging story.

## Safety invariants

- Any state-changing operation requires a concrete preview and explicit user confirmation.
- Approval is bound to the displayed snapshot: target ID, operation, product, amount, and expiry.
- If that snapshot changes, store `STALE_CONFIRMATION`, issue a new `draft_id`, and request approval again.
- Use `draft_id` as the return-application idempotency boundary. A successful retry returns the original application.
- Treat not-found, business conflict, expiry, invalid upstream data, and service failure as different outcomes.
- LangGraph checkpoints coordinate the workflow; the Java business service remains the authority for writes.

## Data and secrets

- Use fictitious IDs and business data in examples and tests.
- Never print, commit, or place `.env` values in state, logs, fixtures, prompts, or documentation.
- Tests must stay offline unless an integration test explicitly documents an external dependency.

## Change workflow

1. Read the smallest relevant production files, tests, and contracts before editing.
2. State the invariant and failure cases that the change must preserve.
3. Make an incremental change; preserve unrelated dirty-worktree changes.
4. Run focused tests first, then the relevant full suite.
5. Read the final traceback from the bottom upward and distinguish test-fixture failures from production failures.
6. Report behavior, verification, and remaining limitations. Do not claim real-model quality from mock tests.

## Local verification

Use the project virtual environment on Windows. Keep tests isolated from local Redis and database settings:

```powershell
$env:REDIS_URL=' '
$env:TICKET_REPOSITORY_BACKEND='memory'
$env:ORDER_QUERY_BACKEND='memory'
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests
```

Run Java tests from `business-service/` with Maven:

```powershell
mvn -B -ntp test
```

Do not commit until the user asks or the agreed project milestone is complete.
