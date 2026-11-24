export const meta = {
  name: "byor-release-stress-test",
  description:
    "Simulated byor users exercise a release candidate in isolated sandboxes, then an independent agent reproduces each finding",
  whenToUse:
    "Before cutting a byor release, from the stress-test-release skill",
  phases: [
    {
      title: "Hunt",
      detail: "one agent per persona uses the candidate in its own sandboxes",
    },
    {
      title: "Verify",
      detail: "one agent per finding reproduces it from a fresh sandbox",
    },
  ],
}

// args: { usersFile, personas: [{ id, scenario }], huntMinutes?, verifyMinutes? }
// Workflow scripts cannot read files, so the skill reads references/personas.md and passes it in.
if (
  !args ||
  !args.usersFile ||
  !Array.isArray(args.personas) ||
  args.personas.length === 0
) {
  throw new Error(
    "args needs usersFile (the filled-in users.md) and a non-empty personas array",
  )
}
const USERS = args.usersFile
const HUNT_MINUTES = args.huntMinutes || 25
const VERIFY_MINUTES = args.verifyMinutes || 10

const FINDINGS = {
  type: "object",
  properties: {
    coverage: {
      type: "string",
      description:
        "What you exercised, 4-10 short lines, including what worked",
    },
    findings: {
      type: "array",
      items: {
        type: "object",
        properties: {
          title: { type: "string" },
          severity: { type: "string", enum: ["high", "medium", "low"] },
          area: {
            type: "string",
            description: "The part of byor the finding is about",
          },
          steps: {
            type: "string",
            description: "Exact shell commands from a fresh sandbox",
          },
          expected: { type: "string" },
          actual: {
            type: "string",
            description: "Verbatim output where it matters",
          },
          introducedBy: {
            type: "string",
            enum: ["this-release", "last-release", "unsure"],
          },
        },
        required: [
          "title",
          "severity",
          "area",
          "steps",
          "expected",
          "actual",
          "introducedBy",
        ],
      },
    },
  },
  required: ["coverage", "findings"],
}

const VERDICT = {
  type: "object",
  properties: {
    reproduced: { type: "boolean" },
    isBug: {
      type: "boolean",
      description:
        "false if it is intended behavior, a harness artifact, or user error",
    },
    introducedBy: {
      type: "string",
      enum: ["this-release", "last-release", "unsure"],
    },
    severity: { type: "string", enum: ["high", "medium", "low"] },
    minimalRepro: { type: "string" },
    notes: {
      type: "string",
      description:
        "Why it is or is not a bug, and the likely cause in src/byor with file:line",
    },
  },
  required: [
    "reproduced",
    "isBug",
    "introducedBy",
    "severity",
    "minimalRepro",
    "notes",
  ],
}

const huntPrompt = (
  p,
) => `Read ${USERS} first and follow it exactly. Your label is "${p.id}"; prefix every sandbox name with "hunt-${p.id}-".

Scenario: ${p.scenario}

Spend about ${HUNT_MINUTES} minutes. Go wide first, then dig into anything that looks wrong. Report only findings you reproduced, with exact commands. Return the coverage summary and your findings; an empty list is a valid result.`

const verifyPrompt = (
  f,
  who,
  i,
) => `Read ${USERS} first and follow it exactly. You are an independent verifier; prefix every sandbox name with "verify-${who}-${i}-".

Another simulated user reported this finding against the release candidate:

Title: ${f.title}
Severity claimed: ${f.severity}
Area: ${f.area}
Steps:
${f.steps}
Expected: ${f.expected}
Actual: ${f.actual}
Claimed origin: ${f.introducedBy}

Reproduce it from a fresh sandbox. Be skeptical: if you cannot reproduce it, if the behavior is intended (check the candidate's docs and source), or if it comes from the harness rather than byor, say so. Check whether $BYOR_LAST behaves the same, to settle whether this release introduced it. Look in the candidate's src/byor for the cause and name file:line if you find it. Spend at most ${VERIFY_MINUTES} minutes. Default to reproduced=false if you are unsure.`

phase("Hunt")
const results = await pipeline(
  args.personas,
  (p) =>
    agent(huntPrompt(p), {
      label: `hunt:${p.id}`,
      phase: "Hunt",
      schema: FINDINGS,
    }),
  (r, p) => {
    if (!r) return { persona: p.id, coverage: "agent failed", findings: [] }
    log(`${p.id}: ${r.findings.length} findings`)
    return parallel(
      r.findings.map(
        (f, i) => () =>
          agent(verifyPrompt(f, p.id, i), {
            label: `verify:${p.id}:${i}`,
            phase: "Verify",
            schema: VERDICT,
          }).then((v) => ({ ...f, verdict: v })),
      ),
    ).then((fs) => ({
      persona: p.id,
      coverage: r.coverage,
      findings: fs.filter(Boolean),
    }))
  },
)
return results.filter(Boolean)
