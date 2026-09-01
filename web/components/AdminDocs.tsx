"use client";

/** 管理面板文档页：读仓库 README.md 并按站点设计体系渲染。 */

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkAlert from "remark-github-blockquote-alert";
import { apiSend } from "@/lib/api";

export function AdminDocs() {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiSend<{ markdown: string }>("/api/docs/readme", "GET")
      .then((data) => setMarkdown(data.markdown))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  if (error) {
    return (
      <div className="panel" style={{ padding: "24px 28px" }}>
        <span style={{ fontSize: 13, color: "var(--text-2)" }}>文档加载失败：{error}</span>
      </div>
    );
  }
  if (markdown === null) {
    return (
      <div className="panel" style={{ padding: "24px 28px" }}>
        <span style={{ color: "var(--text-2)", fontSize: 13 }}>加载中…</span>
      </div>
    );
  }
  return (
    <div className="panel markdown-doc">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkAlert]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
