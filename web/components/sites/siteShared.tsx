"use client";

/** 校验高级配置文本；合法 JSON 对象返回 null，否则返回错误说明。 */
function advancedJsonError(text: string): string | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return "高级配置要是一个 JSON 对象（最外层用 { } 包起来）";
  }
  return null;
}

/** 请求头键值行编辑共用的行结构 */
type KvRow = { key: string; value: string };

/** 键值文本 → 行数组（请求头行编辑的初值） */
function dictToRows(text: string): KvRow[] {
  return Object.entries(textToDict(text)).map(([key, value]) => ({ key, value }));
}

/** 行数组 → 键值文本（"Key: Value" 多行，空行名跳过） */
function rowsToText(rows: KvRow[]): string {
  return rows
    .filter((row) => row.key.trim())
    .map((row) => `${row.key.trim()}: ${row.value}`)
    .join("\n");
}

/** "Key: Value" 或 "Key=Value" 多行文本 → 键值对象；无法解析的行跳过。 */
function textToDict(text: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const match = /^\s*([^:=]+)[:=]\s*(.*)$/.exec(line);
    if (match) result[match[1].trim()] = match[2].trim();
  }
  return result;
}

/** 从采集地址取站点域名（new-api 型续签地址按它推导）；不是合法 URL 返回空串 */
function originOf(text: string): string {
  try {
    return new URL(text.trim()).origin;
  } catch {
    return "";
  }
}

export type { KvRow };
export { advancedJsonError, dictToRows, originOf, rowsToText, textToDict };
