# Project Generic Practices

These practices apply to every project. They are mandatory. A project-specific file can override them.

## Scope

1. Do what the user asked. Keep to the scope of the request.
2. Make routine decisions yourself. Do not ask for a second opinion on a decision that you can make.
3. Ask a question when two readings of the request lead to materially different work.
4. If the request seems wrong, or a better approach exists, say so in one sentence. Then do the task as asked.
5. Finish the whole task. Do not end a turn by announcing the next step. Do the step.
6. Before the task is complete, stop only when you cannot continue without the user, or when a rule in this file requires user approval.
7. Do not change code, structure, or behavior that the current goal does not require.
8. Record unrelated improvements in LOG.md as proposals. Do not implement them.

## Session start

1. Read MEMORY.md.
2. Read README.md. Its "Development" section lists the build, fast-suite, and extended-suite commands.
3. If the "Status" or "Development" section is missing or does not match the project, correct it before other work.

## Architecture Decision Records

1. Write an ADR before you implement a feature, change an interface, or change the architecture.
2. Bug fixes, and changes with no effect on behavior or interfaces, do not need an ADR. Record them in LOG.md.
3. Store ADRs in docs/adr/, numbered in sequence, unless the project already uses a different location.
4. Each ADR states the motivation, the decision points, the options considered, and the success and failure criteria.
5. The project design document maps to a sequence of ADRs.
6. When the ADR work is complete, add an After Action Report (AAR) to the ADR. The AAR measures the effect on the project against the ADR criteria.
7. If the AAR needs data that is not yet available, add it to the "Open AARs" list in MEMORY.md. State the condition that closes it.
8. When that condition is met, update the AAR and remove it from the list.

## Branches and commits

1. Create one branch for each ADR.
2. Commit only the files that you changed on purpose for the current goal.
3. A logical unit of work is complete when the fast suite passes with no regressions.
4. Commit and push the branch after each logical unit of work.
5. Merge the branch when its feature is complete, or when its development stops.
6. To merge, first confirm that README.md matches the branch. Push the branch to its remote tracking branch. Merge it into local master, with fast-forward when possible. Then push master.
7. Do not open pull requests. The local merge is the only merge path.
8. Do not delete feature branches, local or remote. They are the historical record.
9. To remove or roll back a feature, revert commits. Do not comment out or stub out code.

## Tests

1. Use red-green test-driven development. Write the test first. Run it and confirm that it fails. Write the code. Run the test and confirm that it passes.
2. Tests must exercise behavior and properties. A test that checks only structure, names, or other superficial features does not count.
3. Every branch that you add or change must have a test.
4. Write integration tests where components interact.
5. If a branch cannot have a meaningful test, mark it with the coverage tool's exclusion marker. Add a comment that gives the reason. Do not write a trivial test to increase coverage.
6. Put slow tests in a separate extended suite. Keep them out of the fast suite.
7. During feature work, run the extended suite in the background.
8. Wait for the extended suite to pass after major revisions. Also wait for it before a commit that changes behavior the extended suite measures.

## Comments

1. Write comments for a competent developer who does not know the project, its rationale, or its history.
2. Document each public interface: inputs, outputs, units, and errors.
3. Comment on why code exists, its invariants, and constraints that the code does not show.
4. Do not restate what the code does.
5. For design history, cite the ADR number. Do not copy ADR content into comments.

## Documentation

1. LOG.md is the chronological record of development. Add dated entries for process notes, exploration, dead ends, backtracks, and observations. Link to specialized records.
2. Put long notes, such as conversations and design discussions, in docs/log/. Link to them from LOG.md.
3. MEMORY.md holds only high-priority facts that every session needs. Keep it short. Remove facts that are no longer true.
4. LESSONS.md holds durable lessons from the project.
5. README files describe the current state and the entry points. They are not the historical record.
6. README.md has a "Status" section. It states what works, what is in progress, what is broken, and which ADRs are active.
7. A stale README is a defect. Update the "Status" and "Development" sections in the same commit as the change that makes them wrong.
8. Keep each document as short as its content allows. Do not add filler sections or repeated summaries.

## Sub-agents

1. Propose sub-agents only for large tasks that can run in parallel with no cross-branch dependency.
2. Do not propose a sub-agent for work that you can finish in a few tool calls.
3. Do not use sub-agents to verify or review your own work.
4. Get user approval before you launch sub-agents.
5. Sub-agents use the default model and effort unless the user approves more.
6. Give each sub-agent its own branch or worktree, a bounded scope, and explicit success and failure criteria.
7. Sub-agents commit completed logical units locally. They report results before any merge or push.

## Tools

1. Prefer structured tools, such as MCP servers, LSP servers, and existing utilities, when they support the task.
2. Write ad hoc scripts or reimplement existing functions only when structured tools are missing, insufficient, or too much overhead.
3. Custom code is also correct when it documents a specific use case better than a tool does.

## Writing

Apply these rules to messages to the user, plans, and documentation.

1. State the outcome in the first sentence. Put details after it.
2. Write one topic per paragraph. Open each paragraph with its point.
3. Define each term, acronym, and label the first time you use it.
4. Do not refer to anything the user has not seen. Summarize it first.
5. Name code by file path and symbol.
6. Write complete sentences. Do not use arrow chains or fragments in place of prose.
7. Prefer numbers and examples to adjectives.
8. Cut words the reader does not need. Keep words that a reader new to the task needs.
9. Use one word for one meaning.