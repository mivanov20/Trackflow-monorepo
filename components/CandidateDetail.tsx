"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  addCandidateNote,
  candidateErrorMessage,
  candidateName,
  candidateNoteContent,
  candidatePosition,
  deleteCandidateNote,
  formatDate,
  getCandidateNotes,
  humanize,
  replaceCandidate,
  updateCandidateStage,
  updateCandidateStatus,
} from "../lib/candidates";
import { CANDIDATE_STAGES, CANDIDATE_STATUSES, Candidate, CandidateNote, CandidatePayload } from "../types/candidates";
import CandidateForm from "./CandidateForm";

interface CandidateDetailProps {
  initialCandidate: Candidate;
}

const missing = "Not provided";

export default function CandidateDetail({ initialCandidate }: CandidateDetailProps) {
  const [candidate, setCandidate] = useState(initialCandidate);
  const [notes, setNotes] = useState<CandidateNote[]>([]);
  const [noteContent, setNoteContent] = useState("");
  const [notesStatus, setNotesStatus] = useState<"loading" | "success" | "error">("loading");
  const [notesError, setNotesError] = useState("");
  const [notesFeedback, setNotesFeedback] = useState("");
  const [notesAttempt, setNotesAttempt] = useState(0);
  const [pipelineMessage, setPipelineMessage] = useState("Changes are saved automatically.");
  const [pipelineError, setPipelineError] = useState(false);
  const [savingPipeline, setSavingPipeline] = useState(false);
  const [savingNote, setSavingNote] = useState(false);
  const [deletingNoteId, setDeletingNoteId] = useState<string | number | null>(null);
  const [showEditForm, setShowEditForm] = useState(false);
  const [formFeedback, setFormFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  useEffect(() => {
    let active = true;

    async function loadNotes() {
      setNotesStatus("loading");
      setNotesError("");

      try {
        const list = await getCandidateNotes(candidate.id);
        if (active) {
          setNotes(list);
          setNotesStatus("success");
        }
      } catch (error) {
        if (active) {
          setNotesError(candidateErrorMessage(error, "We couldn't load notes."));
          setNotesStatus("error");
        }
      }
    }

    loadNotes();
    return () => {
      active = false;
    };
  }, [candidate.id, notesAttempt]);

  async function updateStatus(status: string) {
    const previous = candidate;
    setCandidate({ ...candidate, status });
    setSavingPipeline(true);
    setPipelineError(false);
    setPipelineMessage("Saving status...");

    try {
      const updated = await updateCandidateStatus(candidate.id, status);
      setCandidate({ ...candidate, ...updated });
      setPipelineMessage("Status saved.");
    } catch (error) {
      setCandidate(previous);
      setPipelineError(true);
      setPipelineMessage(candidateErrorMessage(error, "Status could not be updated."));
    } finally {
      setSavingPipeline(false);
    }
  }

  async function updateStage(stage: string) {
    const previous = candidate;
    setCandidate({ ...candidate, stage });
    setSavingPipeline(true);
    setPipelineError(false);
    setPipelineMessage("Saving stage...");

    try {
      const updated = await updateCandidateStage(candidate.id, stage);
      setCandidate({ ...candidate, ...updated });
      setPipelineMessage("Stage saved.");
    } catch (error) {
      setCandidate(previous);
      setPipelineError(true);
      setPipelineMessage(candidateErrorMessage(error, "Stage could not be updated."));
    } finally {
      setSavingPipeline(false);
    }
  }

  async function submitNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = noteContent.trim();
    if (!content) return;

    setSavingNote(true);
    setNotesError("");
    setNotesFeedback("");

    try {
      await addCandidateNote(candidate.id, content);
    } catch (error) {
      // Solo falla el guardado: el texto se conserva para reintentar, la lista actual sigue visible.
      setNotesError(candidateErrorMessage(error, "The note could not be added. Your text is still in the box."));
      setSavingNote(false);
      return;
    }

    setNoteContent("");
    setNotesFeedback("Note added successfully.");
    try {
      setNotes(await getCandidateNotes(candidate.id));
      setNotesStatus("success");
    } catch (error) {
      // La nota ya se guardó; solo falló recargar la lista, así que no se pide volver a añadirla.
      setNotesError(candidateErrorMessage(error, "The note was saved, but the list could not be refreshed."));
      setNotesStatus("error");
    } finally {
      setSavingNote(false);
    }
  }

  async function removeNote(noteId: string | number) {
    setDeletingNoteId(noteId);
    setNotesError("");
    setNotesFeedback("");

    try {
      await deleteCandidateNote(candidate.id, noteId);
      setNotes((current) => current.filter((note) => note.id !== noteId));
      setNotesStatus("success");
      setNotesFeedback("Note deleted successfully.");
    } catch (error) {
      // La lista sigue siendo válida: se muestra el error sin ocultar las notas.
      setNotesError(candidateErrorMessage(error, "The note could not be deleted."));
    } finally {
      setDeletingNoteId(null);
    }
  }

  async function editCandidate(payload: CandidatePayload) {
    setFormFeedback(null);

    try {
      const updated = await replaceCandidate(candidate.id, payload);
      setCandidate({ ...candidate, ...updated });
      setShowEditForm(false);
      setFormFeedback({ type: "success", message: "Candidate updated successfully." });
    } catch (error) {
      setFormFeedback({ type: "error", message: candidateErrorMessage(error, "Candidate could not be updated.") });
    }
  }

  const notesLoading = notesStatus === "loading";
  const notesSuccess = notesStatus === "success";
  const notesFailed = notesStatus === "error";

  return (
    <>
      <section className="page-header">
        <p className="eyebrow">Candidate detail</p>
        <h1>{candidateName(candidate)}</h1>
        <p className="lede">{candidatePosition(candidate)}</p>
      </section>

      <section className="list-panel detail-panel" aria-labelledby="candidate-data-heading">
        <div className="panel-heading">
          <h2 id="candidate-data-heading">Candidate data</h2>
          <button className="panel-action" type="button" onClick={() => setShowEditForm((current) => !current)}>
            {showEditForm ? "Close form" : "Edit candidate"}
          </button>
        </div>
        {formFeedback ? <p className={formFeedback.type === "success" ? "success-state" : "inline-error"}>{formFeedback.message}</p> : null}
        {showEditForm ? (
          <div className="form-panel">
            <CandidateForm candidate={candidate} submitLabel="Save changes" busyLabel="Saving..." onSubmit={editCandidate} />
          </div>
        ) : null}
        <dl className="data-list">
          <Field label="Name" value={candidateName(candidate)} />
          <Field label="Email" value={candidate.email} href={candidate.email ? `mailto:${candidate.email}` : undefined} />
          <Field label="Phone" value={candidate.phone} />
          <Field label="Position" value={candidatePosition(candidate)} />
          <Field label="LinkedIn" value={candidate.linkedin_url ? "Open LinkedIn" : missing} href={candidate.linkedin_url} />
          <Field label="CV link" value={candidate.cv_url ? "Open CV" : missing} href={candidate.cv_url} />
          <Field label="Years of experience" value={candidate.experience_years} />
          <Field label="Status" value={humanize(candidate.status)} />
          <Field label="Stage" value={humanize(candidate.stage)} />
          <Field label="Application date" value={formatDate(candidate.applied_at || candidate.created_at)} />
        </dl>
      </section>

      <section className="list-panel detail-panel" aria-labelledby="pipeline-heading">
        <div className="panel-heading">
          <h2 id="pipeline-heading">Pipeline</h2>
          <span className={pipelineError ? "feedback-error" : ""}>{pipelineMessage}</span>
        </div>
        <div className="filters two-column-controls">
          <label>
            <span>Update status</span>
            <select value={String(candidate.status || "")} disabled={savingPipeline} onChange={(event) => updateStatus(event.target.value)}>
              {CANDIDATE_STATUSES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Update stage</span>
            <select value={String(candidate.stage || "")} disabled={savingPipeline} onChange={(event) => updateStage(event.target.value)}>
              {CANDIDATE_STAGES.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <section className="list-panel detail-panel" aria-labelledby="notes-heading">
        <div className="panel-heading">
          <h2 id="notes-heading">Notes</h2>
          <span>{notesLoading ? "Loading..." : notesSuccess ? `${notes.length} total` : "Could not load notes"}</span>
        </div>

        <form className="note-form" onSubmit={submitNote}>
          <label htmlFor="noteContent">New note</label>
          <textarea
            id="noteContent"
            rows={4}
            maxLength={1000}
            value={noteContent}
            onChange={(event) => setNoteContent(event.target.value)}
            placeholder="Add an internal note"
          />
          <button type="submit" disabled={savingNote || !noteContent.trim()}>
            {savingNote ? "Adding..." : "Add note"}
          </button>
        </form>

        {notesFeedback ? <p className="success-state padded-feedback">{notesFeedback}</p> : null}
        {notesError ? (
          <div className="error-state" role="alert">
            <p>{notesError}</p>
            {notesFailed ? (
              <button type="button" onClick={() => setNotesAttempt((current) => current + 1)}>
                Reload notes
              </button>
            ) : null}
          </div>
        ) : null}
        {notesLoading ? <p className="empty-state">Loading notes...</p> : null}

        {notesSuccess && notes.length ? (
          <ul className="notes-list">
            {notes.map((note) => (
              <li key={note.id}>
                <p>{candidateNoteContent(note) || missing}</p>
                <div>
                  <span>{formatDate(note.created_at)}</span>
                  <button type="button" disabled={deletingNoteId === note.id} onClick={() => removeNote(note.id)}>
                    {deletingNoteId === note.id ? "Deleting..." : "Delete"}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : null}

        {notesSuccess && !notes.length ? <p className="empty-state">No notes yet.</p> : null}
      </section>
    </>
  );
}

function Field({ label, value, href }: { label: string; value: unknown; href?: string }) {
  const displayValue = value === null || value === undefined || value === "" ? missing : String(value);

  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {href ? (
          <a href={href} target={href.startsWith("http") ? "_blank" : undefined} rel={href.startsWith("http") ? "noreferrer" : undefined}>
            {displayValue}
          </a>
        ) : (
          displayValue
        )}
      </dd>
    </div>
  );
}