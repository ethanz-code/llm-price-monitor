"use client";

/** 公告正文渲染：Markdown 与站点自带的 HTML 片段都支持，经白名单清洗防脚本注入。 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

/**
 * 公告正文允许的 HTML 白名单：站点公告常带 HTML 片段，经 rehype-raw 渲染；
 * 白名单在默认基础上放开 class，保住站点的排版样式，脚本等危险内容仍被剥掉。
 */
const NOTICE_SANITIZE_SCHEMA = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    "*": [...(defaultSchema.attributes?.["*"] ?? []), "className"],
  },
};

/** 公告正文：站点详情页与事件详情弹窗共用，保证两处渲染一致。 */
export function NoticeBody({ content, style }: { content: string; style?: React.CSSProperties }) {
  return (
    <div className="notice-body" style={{ fontSize: 13.5, lineHeight: 1.75, ...style }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw, [rehypeSanitize, NOTICE_SANITIZE_SCHEMA]]}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
