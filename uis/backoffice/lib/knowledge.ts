import { apiRequest } from "./api-client";

/** The contract of `POST /knowledge/query`: the answer the model wrote, nothing else (no chunks, no scores). */
export interface KnowledgeAnswer {
  answer: string;
}

export const MAX_QUESTION_LENGTH = 1000;

/** Example questions of the commercial team, one per source document. */
export const EXAMPLE_QUESTIONS = [
  "What's the standard return window?",
  "Which carrier best covers rural Aragón?",
  "Can we promise delivery times to a client during Black Friday?",
  "How is storage priced in Zaragoza?",
] as const;

/** Question as it is sent: no surrounding whitespace. An empty or over-long question is rejected before any request. */
export function cleanQuestion(raw: string): { question: string; problem: string } {
  const question = raw.trim();
  if (!question) return { question, problem: "Type a question first." };
  if (question.length > MAX_QUESTION_LENGTH) return { question, problem: `The question is too long (the limit is ${MAX_QUESTION_LENGTH} characters).` };
  return { question, problem: "" };
}

export const knowledgeApi = {
  /** Asks the knowledge base. Generating the answer can take several seconds, so the timeout is generous. */
  ask(token: string, question: string): Promise<KnowledgeAnswer> {
    return apiRequest<KnowledgeAnswer>("/knowledge/query", { method: "POST", token, body: { question }, timeoutMs: 60_000, serviceName: "knowledge assistant" });
  },
};
