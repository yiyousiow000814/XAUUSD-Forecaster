import { env } from "cloudflare:workers";
import { NextResponse } from "next/server";
import { isIngestAuthorized } from "../_shared/ingest-auth";
import { previewBundle, previewJson, rejectPreviewWrite } from "../_shared/preview";

export const dynamic = "force-dynamic";

const publicRow = (row: Record<string, unknown>) => ({
  id: row.id, question: row.question, status: row.status, asked_at: row.asked_at,
  answer: row.answer, evidence_ids: row.evidence_json ? JSON.parse(String(row.evidence_json)) : [],
  answered_at: row.answered_at, model_version: row.model_version,
});

export async function GET(request: Request) {
  if (previewBundle) return previewJson({ items: [], preview: true });
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "新闻问答暂不可用" }, { status: 503 });
  const params = new URL(request.url).searchParams;
  const id = params.get("id") ?? "";
  if (id) {
    const row = await binding.prepare("SELECT * FROM news_questions WHERE id=?").bind(id).first<Record<string, unknown>>();
    return row ? NextResponse.json(publicRow(row)) : NextResponse.json({ error: "找不到这个问题" }, { status: 404 });
  }
  if (!await isIngestAuthorized(request)) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const rows = await binding.prepare("SELECT id,question,asked_at FROM news_questions WHERE status='PENDING' ORDER BY asked_at LIMIT 3").all();
  return NextResponse.json({ items: rows.results });
}

export async function POST(request: Request) {
  const previewRejection = rejectPreviewWrite();
  if (previewRejection) return previewRejection;
  const binding = env.DB as D1Database | undefined;
  if (!binding) return NextResponse.json({ error: "新闻问答暂不可用" }, { status: 503 });
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > 10_000) return NextResponse.json({ error: "内容过长" }, { status: 413 });
  try {
    const body = JSON.parse(raw) as Record<string, unknown>;
    if (typeof body.answer === "string") {
      if (!await isIngestAuthorized(request)) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
      const evidence = Array.isArray(body.evidence_ids) ? body.evidence_ids.slice(0, 12).map(String) : [];
      const result = await binding.prepare("UPDATE news_questions SET status='ANSWERED',answer=?,evidence_json=?,answered_at=?,model_version=? WHERE id=? AND status='PENDING'")
        .bind(body.answer.slice(0, 4000), JSON.stringify(evidence), new Date().toISOString(), String(body.model_version ?? "Gemma 4"), String(body.id ?? "")).run();
      return NextResponse.json({ status: "OK", updated: result.meta.changes });
    }
    const question = String(body.question ?? "").trim().replace(/\s+/g, " ");
    if (question.length < 4 || question.length > 200) return NextResponse.json({ error: "问题需要4至200个字" }, { status: 400 });
    const pending = await binding.prepare("SELECT count(*) count FROM news_questions WHERE status='PENDING'").first<{ count: number }>();
    if ((pending?.count ?? 0) >= 10) return NextResponse.json({ error: "当前问题较多，请稍后再试" }, { status: 429 });
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(
      `${new Date().toISOString().slice(0, 10)}\n${question.toLocaleLowerCase("zh-CN")}`,
    ));
    const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    const existing = await binding.prepare("SELECT * FROM news_questions WHERE question_hash=?").bind(hash).first<Record<string, unknown>>();
    if (existing) return NextResponse.json(publicRow(existing));
    const id = crypto.randomUUID();
    const askedAt = new Date().toISOString();
    await binding.prepare("INSERT INTO news_questions (id,question_hash,question,status,asked_at) VALUES (?,?,?,'PENDING',?)")
      .bind(id, hash, question, askedAt).run();
    return NextResponse.json({ id, question, status: "PENDING", asked_at: askedAt }, { status: 202 });
  } catch {
    return NextResponse.json({ error: "无法提交问题" }, { status: 400 });
  }
}
