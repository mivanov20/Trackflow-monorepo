"use client";

import { useRef, useState, type FormEvent } from "react";
import { GENERIC_ERROR_MESSAGE, messageFor } from "../../../lib/api-client";
import { getAccessToken } from "../../../lib/auth";
import { EXAMPLE_QUESTIONS, MAX_QUESTION_LENGTH, cleanQuestion, knowledgeApi } from "../../../lib/knowledge";

/** Ask the TrackFlow knowledge base (delivery SLA, returns, carriers, storage pricing) and read the answer the assistant writes. */
export default function KnowledgeAssistantPage() {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [asked, setAsked] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [error, setError] = useState("");
  const latest = useRef(0);

  async function submit(raw: string) {
    const { question, problem } = cleanQuestion(raw);
    if (problem) {
      setError(problem);
      return;
    }
    const token = getAccessToken();
    if (!token) return;
    const request = ++latest.current;
    setLoading(true);
    setError("");
    setAnswer(null);
    setAsked(question);
    try {
      const result = await knowledgeApi.ask(token, question);
      if (request === latest.current) setAnswer(result.answer);
    } catch (caught) {
      // A failed call is an error message, never an empty answer.
      if (request === latest.current) setError(`The assistant could not answer. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    } finally {
      if (request === latest.current) setLoading(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit(text);
  }

  return (
    <>
      <header className="inventory-page-header">
        <div>
          <p className="inventory-eyebrow">KNOWLEDGE BASE</p>
          <h1>Ask TrackFlow</h1>
          <p>Ask about delivery SLA, returns, carrier coverage or storage pricing. The answer is written from TrackFlow&apos;s standard documents, so you can say it to a client with exact data.</p>
        </div>
      </header>
      <section className="inventory-form-section">
        <form className="incident-form" onSubmit={onSubmit} noValidate>
          <label htmlFor="knowledge-question"><strong>Your question</strong></label>
          <textarea
            id="knowledge-question"
            rows={3}
            maxLength={MAX_QUESTION_LENGTH}
            value={text}
            disabled={loading}
            placeholder="For example: What's the standard return window?"
            onChange={(event) => setText(event.target.value)}
            aria-describedby="knowledge-hint"
          />
          <p id="knowledge-hint" className="inventory-hint">{text.length} / {MAX_QUESTION_LENGTH} characters</p>
          <div className="analysis-period">
            <button className="inventory-primary-button" type="submit" disabled={loading || !text.trim()}>{loading ? "Looking it up…" : "Ask"}</button>
          </div>
        </form>
        <p className="inventory-hint"><strong>Try:</strong></p>
        <div className="analysis-period">
          {EXAMPLE_QUESTIONS.map((example) => (
            <button key={example} className="inventory-secondary-button" type="button" disabled={loading} onClick={() => { setText(example); void submit(example); }}>{example}</button>
          ))}
        </div>
      </section>
      {loading && <p className="inventory-hint" role="status">Looking it up in the knowledge base…</p>}
      {error && <p className="inventory-alert" role="alert">{error}</p>}
      {answer !== null && (
        <section className="inventory-table-section analysis-panel" aria-label="Answer" aria-live="polite">
          <h2>Answer</h2>
          <p className="incident-muted">Your question: {asked}</p>
          <div className="knowledge-answer">{answer.split("\n").map((line, index) => (line.trim() ? <p key={index}>{line}</p> : null))}</div>
          <p className="incident-muted">Written by the assistant from the documents listed under &quot;Source&quot;. If it says it has no information, confirm with the responsible area before promising anything to a client.</p>
        </section>
      )}
    </>
  );
}
