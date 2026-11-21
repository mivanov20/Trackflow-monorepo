import type { Metadata } from "next";
import Image from "next/image";
import briefOne from "../../assets/M4 photo1.png";
import briefTwo from "../../assets/M4 photo2.png";
import briefThree from "../../assets/M4photo3.png";
import briefFour from "../../assets/M4photo4.png";

export const metadata: Metadata = {
  title: "AI-driven Engineering | Working with AI Coding Agents",
  description:
    "A practical guide to repository context, memory banks, implementation, verification, and submission for the AI-driven Engineering milestone.",
};

const contents = [
  ["challenge", "Your challenge"],
  ["memory-setup", "Memory bank setup"],
  ["rules-agents-skills", "Rules, agents & skills"],
  ["attention", "Attention"],
  ["start", "How to start"],
  ["structure", "Application structure"],
  ["evaluation", "What will be evaluated"],
  ["submit", "How to submit"],
] as const;

const screenshots = [
  { image: briefOne, label: "Brief 01", alt: "First page of the Milestone 4 assignment brief" },
  { image: briefTwo, label: "Brief 02", alt: "Second page of the Milestone 4 assignment brief" },
  { image: briefThree, label: "Brief 03", alt: "Third page of the Milestone 4 assignment brief" },
  { image: briefFour, label: "Brief 04", alt: "Fourth page of the Milestone 4 assignment brief" },
];

export default function AiDrivenEngineeringPage() {
  return (
    <main className="milestone-guide">
      <header className="guide-masthead">
        <a className="guide-wordmark" href="#top" aria-label="AI-driven Engineering home">
          <span className="wordmark-mark">AI</span>
          <span>Working with AI coding agents</span>
        </a>
        <span className="masthead-course">Course guide <span>/</span> Milestone 04</span>
      </header>

      <div className="guide-layout" id="top">
        <aside className="guide-sidebar" aria-label="Page contents">
          <p className="sidebar-label">On this page</p>
          <nav>
            {contents.map(([id, label], index) => (
              <a href={`#${id}`} key={id}>
                <span>{String(index + 1).padStart(2, "0")}</span>{label}
              </a>
            ))}
          </nav>
          <div className="sidebar-note">
            <span className="status-dot" />
            <p>Read the repository first. Let its context guide the work.</p>
          </div>
        </aside>

        <article className="guide-content">
          <section className="guide-hero" aria-labelledby="guide-title">
            <p className="guide-kicker">Course / AI-driven Engineering</p>
            <h1 id="guide-title">Build with context.<br /><em>Ship with confidence.</em></h1>
            <p className="guide-intro">
              A coding agent is most useful when it understands the system it is changing.
              This milestone puts repository knowledge, careful implementation, and verification
              at the center of the engineering process.
            </p>
            <div className="workflow-strip" aria-label="Engineering workflow">
              <span>Understand</span><b aria-hidden="true">/</b>
              <span>Plan</span><b aria-hidden="true">/</b>
              <span>Implement</span><b aria-hidden="true">/</b>
              <span>Verify</span><b aria-hidden="true">/</b>
              <span>Submit</span>
            </div>
          </section>

          <section className="guide-section challenge-section" id="challenge">
            <div className="section-heading">
              <span className="section-index">01</span>
              <div><p className="section-kicker">The assignment</p><h2>Your challenge</h2></div>
            </div>
            <p className="section-lede">
              Work as an AI coding agent inside a company repository. Understand the codebase,
              use its documentation and conventions, then complete the assigned engineering task
              with focused changes and clear evidence that the result works.
            </p>
            <div className="callout-line">
              <span className="callout-symbol">!</span>
              <p><strong>Before editing:</strong> read <code>CONTEXT.md</code> and the relevant project documentation. Treat repository facts as the source of truth; don’t fill gaps with assumptions.</p>
            </div>
          </section>

          <section className="guide-section" id="memory-setup">
            <div className="section-heading">
              <span className="section-index">02</span>
              <div><p className="section-kicker">Carry knowledge forward</p><h2>Memory bank setup</h2></div>
            </div>
            <p>
              A memory bank keeps durable project knowledge close to future work. Record the
              details an agent would otherwise have to rediscover, and update them when the
              codebase or decisions change.
            </p>
            <div className="memory-grid">
              <article><span className="memory-number">01</span><h3>Project map</h3><p>What the application does, its main folders, and where important behavior lives.</p></article>
              <article><span className="memory-number">02</span><h3>Working conventions</h3><p>Framework patterns, naming, formatting, testing expectations, and boundaries.</p></article>
              <article><span className="memory-number">03</span><h3>Useful commands</h3><p>Known-good setup, development, test, typecheck, and build commands.</p></article>
              <article><span className="memory-number">04</span><h3>Decisions & constraints</h3><p>Architecture choices, domain rules, integrations, and things that must not change.</p></article>
            </div>
          </section>

          <section className="guide-section" id="rules-agents-skills">
            <div className="section-heading">
              <span className="section-index">03</span>
              <div><p className="section-kicker">Make good work repeatable</p><h2>Memory bank: rules, agents &amp; skills</h2></div>
            </div>
            <div className="definition-list">
              <div><h3>Memory</h3><p>Persistent facts and decisions that help an agent reason accurately across tasks and sessions.</p></div>
              <div><h3>Rules</h3><p>Always-on guidance for how work in this repository should be approached, changed, and verified.</p></div>
              <div><h3>Agents</h3><p>Focused roles with a clear responsibility, scope, and set of tools for a repeatable kind of work.</p></div>
              <div><h3>Skills</h3><p>Reusable procedures and references that teach an agent how to complete a specific workflow.</p></div>
            </div>
            <p className="section-tail">Together they reduce rediscovery, keep work consistent, and make the agent’s decisions easier to review.</p>
          </section>

          <section className="attention-panel" id="attention">
            <div className="attention-label"><span aria-hidden="true">!</span><p>Attention</p></div>
            <div>
              <h2>Don’t start by changing files.</h2>
              <p>First understand the repository and its context. Then identify the owning code path, make a focused plan, implement the smallest complete change, and verify it before submitting.</p>
              <p className="attention-sequence">Context <span>→</span> Plan <span>→</span> Change <span>→</span> Check <span>→</span> Submit</p>
            </div>
          </section>

          <section className="guide-section" id="start">
            <div className="section-heading">
              <span className="section-index">04</span>
              <div><p className="section-kicker">A disciplined first pass</p><h2>How to start the project</h2></div>
            </div>
            <ol className="step-list">
              <li><span>01</span><div><h3>Read the context</h3><p>Start with <code>CONTEXT.md</code>, the root README, and instructions that apply to the files you may touch.</p></div></li>
              <li><span>02</span><div><h3>Map the repository</h3><p>Review its structure and identify the app, services, shared code, and documentation relevant to the task.</p></div></li>
              <li><span>03</span><div><h3>Trace the behavior</h3><p>Read the closest implementation, call sites, and tests. Follow established patterns instead of inventing new ones.</p></div></li>
              <li><span>04</span><div><h3>Choose a focused check</h3><p>Before editing, know what test, typecheck, or other quick validation will confirm or challenge your approach.</p></div></li>
              <li><span>05</span><div><h3>Implement and verify</h3><p>Make the scoped change, run the focused check, then verify the wider application when appropriate.</p></div></li>
            </ol>
          </section>

          <section className="guide-section" id="structure">
            <div className="section-heading">
              <span className="section-index">05</span>
              <div><p className="section-kicker">Find the right home for a change</p><h2>Application structure</h2></div>
            </div>
            <p>Understand how the existing application is organized before choosing where new work belongs.</p>
            <div className="structure-rules">
              <p><span>01</span> Find the entry point and follow the current feature flow.</p>
              <p><span>02</span> Identify the component or module that owns the behavior.</p>
              <p><span>03</span> Keep shared contracts and domain logic in their established layers.</p>
              <p><span>04</span> Place tests, documentation, and assets alongside the conventions already in use.</p>
            </div>
          </section>

          <section className="guide-section" id="evaluation">
            <div className="section-heading">
              <span className="section-index">06</span>
              <div><p className="section-kicker">Quality, not just output</p><h2>What will be evaluated</h2></div>
            </div>
            <ul className="evaluation-list">
              <li><span aria-hidden="true">✓</span><p>The requested behavior is implemented correctly and the existing application still works.</p></li>
              <li><span aria-hidden="true">✓</span><p>Changes are focused, maintainable, and consistent with repository conventions.</p></li>
              <li><span aria-hidden="true">✓</span><p>The implementation touches the appropriate files and avoids unnecessary scope.</p></li>
              <li><span aria-hidden="true">✓</span><p>Relevant tests or checks were run, and their results are reported accurately.</p></li>
              <li><span aria-hidden="true">✓</span><p>Repository context and memory are used to guide decisions rather than bypassed.</p></li>
              <li><span aria-hidden="true">✓</span><p>The work shows a clear process: understand, plan, implement, verify, and communicate.</p></li>
            </ul>
          </section>

          <section className="guide-section submit-section" id="submit">
            <div className="section-heading">
              <span className="section-index">07</span>
              <div><p className="section-kicker">Close the loop</p><h2>How to submit</h2></div>
            </div>
            <div className="submit-checklist">
              <p><span>01</span> Confirm the requested work is complete.</p>
              <p><span>02</span> Run the appropriate tests, checks, or application flow.</p>
              <p><span>03</span> Review the final changes for scope and repository state.</p>
              <p><span>04</span> Submit through the course platform and summarize what changed and how it was verified.</p>
            </div>
          </section>

          <section className="brief-reference" aria-labelledby="brief-title">
            <div className="reference-heading"><div><p className="section-kicker">Source material</p><h2 id="brief-title">Milestone brief</h2></div><p>Four pages provided with the assignment</p></div>
            <div className="brief-gallery">
              {screenshots.map(({ image, label, alt }) => (
                <a href={image.src} target="_blank" rel="noreferrer" key={label} aria-label={`Open ${label} screenshot`}>
                  <Image src={image} alt={alt} />
                  <span>{label}<span aria-hidden="true"> ↗</span></span>
                </a>
              ))}
            </div>
          </section>

          <footer className="guide-footer">
            <p>AI-driven Engineering <span>·</span> Working with AI coding agents</p>
            <a href="#top">Back to top ↑</a>
          </footer>
        </article>
      </div>
    </main>
  );
}