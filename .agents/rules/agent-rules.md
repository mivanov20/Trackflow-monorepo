**Scope:** All repository files (`**/*`). The root `AGENTS.md` is the canonical source; keep this scoped rule synchronized with it.

# Universal Agent Rules

These rules apply to AI agents working in this repository. Follow them together with the repository-specific workflow below and any more specific instructions. Preserve user intent when instructions conflict; ask for clarification when the conflict changes the implementation. All user-facing communication is in English.

## 1. Communication

- Respond in English.
- Write code identifiers, filenames, and class names in English.
- Write code comments in English when comments are needed; do not add comments that conflict with more specific repository or task requirements.
- Write commit messages in English using Conventional Commits.
- Keep communication direct and technical.
- Use English branch names, such as `feat/user-auth` or `fix/login-redirect`.

## 2. Workflow

For changes that modify repository files, follow this sequence:

1. **Read:** Read applicable local agent instructions and relevant project files before editing.
2. **Plan:** State the behavior to change, affected files, approach, and focused validation.
3. **Wait:** Obtain explicit user confirmation before making the planned changes.
4. **Execute:** Make surgical changes limited to the confirmed scope.
5. **Report:** Provide the verification report described in Section 8.

Always cite the exact file and line for changes in the final report. If scope is ambiguous or a domain conflict affects the implementation, ask before assuming. Do not break existing functionality without warning.

At the start of every session, read `CONTEXT.md`, `memory-bank/projectbrief.md`, `memory-bank/techContext.md`, and `memory-bank/progress.md` before making company-specific or architectural decisions.

## 3. Prohibitions

Unless the user explicitly instructs otherwise:

- Do not refactor outside the requested scope, rename existing symbols, change project structure, install or alter dependencies, or change function signatures unnecessarily.
- Do not create files outside the requested scope. `TECH_DEBT.md` is the sole exception when Section 10 requires recording detected out-of-scope debt and the file does not exist.
- Do not propose or execute commits.
- Do not add debug prints such as `console.log`.

The following paths require explicit developer confirmation before modification: `CONTEXT.md`, `uis/website/TRACKFLOW_README.md`, `package.json`, package-manager lockfiles, `.env*` files or secret stores, `infra/**`, deployment configuration, and database migrations. This does not override an explicit user request to change one of these paths.

## 4. Obligations

- Read all relevant files before proposing a change.
- Propose focused tests or checks alongside each implementation.
- Provide the verification report in Section 8 at the end of each completed task.
- Add English comments only when comments are appropriate; avoid comments that merely restate code.
- Add JSDoc to public JavaScript functions and docstrings to Python functions when creating or materially changing them, consistent with local conventions.
- Record out-of-scope technical debt in `TECH_DEBT.md` as described in Section 10.
- Perform the security review in Section 7 for every task.

## 5. Code Conventions

- Prefer the language and stack already used by the owning project. Use JavaScript by default only where the project does not establish another language; do not introduce TypeScript unless the project already uses it.
- Prefer functional components unless the existing architecture or task justifies another pattern.
- Follow the existing folder structure and export conventions rather than reorganizing the project.
- Handle asynchronous work without leaving rejected promises unhandled. Use the error-handling conventions of the owning application.
- Use the styling system already present in the project.
- Validate inputs at public boundaries and API endpoints. Handle errors without exposing stack traces to clients; use generic client messages and appropriate internal diagnostics for critical failures.

## 6. Git

- Follow the repository's branch workflow; when no repository-specific workflow exists, use `main` → `dev` → feature branches.
- Use English Conventional Commit messages, for example `feat: add user authentication`, `fix: resolve token expiration bug`, `docs: add API documentation`, or `test: add unit tests for user service`.
- Do not propose or create commits unless explicitly requested.
- Before a major refactor, suggest a checkpoint commit to the user; do not create it without approval.

## 7. Security Review

Review each task for:

- Sensitive data exposed to clients.
- Unvalidated inputs.
- Hardcoded environment-specific configuration or secrets.
- Errors that expose internal details.
- Injection risks, including SQL and XSS.
- Secrets or credentials stored in source code instead of an appropriate secret store or environment configuration.

If a vulnerability is found, report it promptly and prioritize mitigation. Do not introduce secrets or expose internal stack traces.

## 8. Verification Report

End each completed task with an English report:

```markdown
## Verification Report

### Files Changed
| File | Lines changed | Reason |
|---|---|---|
| path/to/file | line | description |

### Summary
What was done and why (2-4 lines).

### Compliance Checklist
| Criterion | Status |
|---|---|
| Scope respected | ✅ / ❌ |
| Tests proposed and run | ✅ / ❌ |
| Comments added where appropriate | ✅ / ❌ |
| Out-of-scope technical debt | ✅ none / ⚠️ see TECH_DEBT.md |
| Security reviewed | ✅ / ⚠️ description |
| No broken functionality | ✅ / ❌ |

### Suggested Next Steps
- ...
```

Report actual test results; never claim an unrun check passed. Mark unavailable or inapplicable checks and explain why.

## 9. Testing

- Testing is mandatory for implementation tasks. Propose the relevant test or validation before implementation and run it before reporting completion.
- If the project has no test setup, identify that gap and propose an appropriate check or test setup before implementing a feature. Do not add dependencies without explicit authorization.
- Prefer the tools already configured by the project. Typical choices include Vitest for Vite/React, Jest for other JavaScript projects, Testing Library for components, and Cypress or Playwright for end-to-end coverage.

## 10. Technical Debt

- Report out-of-scope bugs without modifying them.
- Record detected out-of-scope technical debt in the root `TECH_DEBT.md`; create it only when necessary and if it does not already exist.
- Use this format:

```text
- [ ] [src/file.js:42] Description of the detected problem — detected on: YYYY-MM-DD
```

- Do not interrupt the requested task to fix unrelated debt.

## 11. Active Skills

Apply an available skill when it is relevant to the task. The following names are suggested skill areas; use the actual skill instructions available in the environment and do not claim to have applied an unavailable skill.

- **Core:** `vercel-react-best-practices`, `next-best-practices`, `vercel-composition-patterns`, `deploy-to-vercel`, `web-design-guidelines`, `supabase-postgres-best-practices`, `frontend-design`, `shadcn`, `typescript-advanced-types`, `nodejs-backend-patterns`, `api-design-principles`, `modern-javascript-patterns`, `postgresql-table-design`, `better-auth-best-practices`, `performance`.
- **Recommended:** `systematic-debugging`, `test-driven-development`, `requesting-code-review`, `receiving-code-review`, `verification-before-completion`, `improve-codebase-architecture`, `tdd`, `grill-me`, `python-testing-patterns`, `javascript-testing-patterns`, `e2e-testing-patterns`, `sql-optimization-patterns`, `python-performance-optimization`, `api-security-best-practices`, `typescript-expert`, `webapp-testing`.
- **Additional:** `write-a-prd`, `prd-to-issues`, `brainstorming`, `writing-plans`, `executing-plans`, `using-git-worktrees`, `finishing-a-development-branch`, `dispatching-parallel-agents`, `subagent-driven-development`, `self-improving-agent`, `git-commit`, `frontend-design`, `overdrive`, `openapi-spec-generation`, `security-requirement-extraction`.
- **Security:** `security-best-practices`, `api-security-best-practices`, `security-requirement-extraction`, `gdpr-data-handling`, `k8s-security-policies`, `solidity-security`, `skill-vetter`.
- **Mobile:** `vercel-react-native-skills`, `building-native-ui`, `native-data-fetching`, `expo-tailwind-setup`, `expo-api-routes`, `expo-cicd-workflows`, `expo-deployment`, `swiftui-expert-skill`, `swift-concurrency`, `swiftui-performance-audit`, `mobile-android-design`, `flutter-expert`, `flutter-animations`, `ui-ux-pro-max`, `sleek-design-mobile-apps`, `mobile-ios-design`, `fixing-motion-performance`.

# Repository Agent Workflow

These instructions apply to coding agents working anywhere in this repository.

## Before changing files

1. Read `CONTEXT.md` and the relevant files in `memory-bank/` before making company-specific or architectural decisions.
2. Read the root `README.md` and the `README.md` for each repository area you plan to change. Distinguish development configuration in `.agents/` from product agents in `agents/` and product skills in `skills/`.
3. Inspect the current implementation, nearby call sites/tests, and `git status`. Preserve existing user changes and avoid unrelated cleanup.
4. State the behavior to change, the smallest likely owning code path, and a focused check before editing. Ask the project owner when an unresolved domain conflict would change the implementation.

## While implementing

- Follow `.agents/rules/monorepo-conventions.md` and any more specific instructions that apply to the touched area.
- Keep changes within the canonical monorepo folders and use the stack already present there.
- Prefer focused, reversible changes. Do not invent business facts, API contracts, credentials, or operational policies.
- After each substantive edit, run the narrowest available test, typecheck, build, or runtime check before widening the change.
- Update the relevant memory-bank files when verified architecture, commands, constraints, or project state changes.

## Before committing or opening a pull request

1. Run the checks required by `.agents/skills/verify-delivery/SKILL.md` for every changed application.
2. Review `git diff --check`, `git status`, and the complete diff. Confirm no generated output, secrets, unrelated files, or unverified claims are included.
3. Confirm memory-bank state and documentation match the delivered changes; report tests that could not run.
4. Commit and push only when the task owner has requested submission. Never amend or force-push shared history without explicit approval.
5. For an explicitly requested pull request, target the repository's default branch, summarize behavior and validation, and report the PR URL. Do not claim a PR exists until the hosting service confirms creation.