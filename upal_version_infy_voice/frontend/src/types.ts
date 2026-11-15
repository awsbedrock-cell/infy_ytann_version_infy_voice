export interface Block {
  type: "paragraph" | "heading" | "question" | "activity" | "table" | "figure";
  text: string;
  level?: number;
}

export interface Page {
  page: number;
  image?: string;
  has_photo?: boolean;
  blocks: Block[];
}

export interface QuizQ {
  q: string;
  options: string[];
  answer: number;
  explain: string;
}

export interface Flashcard {
  front: string;
  back: string;
}

export interface AskQA {
  q: string;
  a: string;
}

export interface Chapter {
  id: number;
  slug: string;
  subject: string;
  book: string;
  chapter_number: number;
  title: string;
  summary: string;
  grade: number;
  course_id: number;
  key_points: (string | { text: string })[];
  pages: Page[];
  quiz: QuizQ[];
  flashcards: Flashcard[];
  ask_qa: AskQA[];
  fun_facts: string[];
}

export interface MintResponse {
  token: string;
  ws_url: string;
  model: string;
  voice?: string;
  system_instruction?: string;
  greeting?: string;
  max_session_minutes?: number;
  vad?: Record<string, unknown>;
  session_resumption?: boolean;
}
