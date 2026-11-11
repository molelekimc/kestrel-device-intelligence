// "About this" panel: the business problem and how the project is built, for a first-time visitor
// (a plant manager, a recruiter or a hiring manager). Plain language; the detail lives in the README.
import { useRef } from "react";
import { BrandMark } from "./controlroom";

export function About() {
  const dialog = useRef<HTMLDialogElement>(null);
  return (
    <>
      <button className="ghost about-btn" onClick={() => dialog.current?.showModal()}>About this</button>
      <dialog ref={dialog} className="about" aria-labelledby="about-title" onClick={(e) => e.target === dialog.current && dialog.current?.close()}>
        <div className="about-body">
          <div className="about-head">
            <BrandMark size={34} />
            <h2 id="about-title">What this is</h2>
            <button className="ghost" onClick={() => dialog.current?.close()} aria-label="Close">✕</button>
          </div>

          <h3>The problem</h3>
          <p>
            Kestrel Assembly Group builds vehicles and components on 52 production lines in three plants. Every line
            depends on Windows PCs running the shop-floor software (MES). When those PCs crash, the line stops. Nobody
            could say how much production IT faults cost, what caused them, or whether the fixes worked, because the
            answers sat in six systems that don't talk to each other.
          </p>

          <h3>What you're looking at</h3>
          <ul>
            <li><strong>Control room:</strong> the week in one sentence, the plant floor coloured by how much stopped time IT caused, the cost, and the fixes.</li>
            <li><strong>TV mode:</strong> the same story for a wall screen, rotating every 20 seconds.</li>
            <li><strong>Detail pages:</strong> the evidence behind every number, for analysts.</li>
            <li><strong>Ask the fleet:</strong> plain-English questions, answered by Claude through the same governed data.</li>
          </ul>

          <h3>Why you can trust it</h3>
          <ul>
            <li>Every number comes from one set of governed tools: aggregates only, small groups hidden, no device names or people.</li>
            <li>Causes are called <em>leads</em>, not proof, and each comes with the evidence behind it.</li>
            <li>When data is late or missing, every screen says so. A silent plant is never shown as a healthy one.</li>
            <li>Costs are illustrative ranges from written-down assumptions, not finance figures.</li>
            <li>Tests and an evaluation harness check the answers against a known answer key.</li>
          </ul>

          <h3>How it's built</h3>
          <p>
            Python and DuckDB for the data pipeline, a read-only MCP server for AI access, FastAPI and React/TypeScript for
            this app, and GitHub Actions for checks and the weekly report. The data refreshes itself as new files arrive.
            It was built with Claude as a coding partner, with every phase planned, reviewed and tested before moving on.
          </p>

          <p className="about-foot">
            Ample Roots Digital case study 01. Kestrel Assembly Group is a fictional client and all data is synthetic.
            The laptop image is AI-generated.
          </p>
        </div>
      </dialog>
    </>
  );
}
