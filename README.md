# Role Catcher

Scans open roles across Greenhouse, Ashby, and Lever job boards, scores each against my background, vets the company, and emails me only roles that pass both tests. It does not write cover letters or apply for me.

## Pipeline (cheap steps first, expensive steps last)

1. **Discover**: collect open roles from as many Greenhouse, Ashby, and Lever boards as practical.
2. **Pre-filter (code, no AI)**: title, seniority, and location rules.
3. **Score role fit (LLM via OpenRouter)** against my profile. Threshold 75, configurable.
4. **Vet company**, only for roles that passed step 3. Verdicts cached 60 days.
5. **Email**, only if role fit AND company vet both pass.

Never vet a company or score a role that an earlier cheap step already rejected.

All AI calls go through OpenRouter.

## Status

Build in progress (prompt 1 of 9: repo reset and pipeline defined).
