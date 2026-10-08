import styles from "./admin-feedback-panel.module.css";

function MetadataValue({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null) return <span className={styles.metadataEmpty}>null</span>;
  if (typeof value !== "object") {
    return <span className={styles.metadataValue}>{typeof value === "string" ? (value || '""') : String(value)}</span>;
  }
  // Extremely deep payloads remain readable without unbounded nested layout.
  if (depth >= 6) return <pre className={styles.metadataRaw}>{JSON.stringify(value, null, 2)}</pre>;
  if (Array.isArray(value)) {
    return value.length ? <ol className={styles.metadataArray}>{value.map((item, index) =>
      <li key={index}><MetadataValue value={item} depth={depth + 1} /></li>
    )}</ol> : <span className={styles.metadataEmpty}>빈 배열 []</span>;
  }
  const entries = Object.entries(value);
  return entries.length ? <dl className={styles.metadataRows}>{entries.map(([key, item]) =>
    <div key={key}><dt><code>{key}</code></dt><dd><MetadataValue value={item} depth={depth + 1} /></dd></div>
  )}</dl> : <span className={styles.metadataEmpty}>빈 객체 { "{}" }</span>;
}

export function AdminFeedbackMetadata({ sessionId, answerId, messageId, metadata }: {
  sessionId: string;
  answerId: string;
  messageId: number;
  metadata: Record<string, unknown>;
}) {
  return <details className={styles.technical}>
    <summary>기술 정보 및 저장 메타데이터</summary>
    <div className={styles.technicalBody}>
      <h3>답변 식별 정보</h3>
      <dl className={styles.identifierRows}>
        <div><dt>세션 ID</dt><dd><code>{sessionId}</code></dd></div>
        <div><dt>실제 답변 ID</dt><dd><code>{answerId}</code></dd></div>
        <div><dt>공개 번호</dt><dd><code>{messageId}</code></dd></div>
      </dl>
      <h3>저장 메타데이터</h3>
      {Object.keys(metadata).length ? <MetadataValue value={metadata} /> :
        <p className={styles.metadataEmpty}>저장된 메타데이터가 없어요.</p>}
      <details className={styles.rawDisclosure}>
        <summary>원본 JSON 보기</summary>
        <pre className={styles.metadataRaw}>{JSON.stringify(metadata, null, 2)}</pre>
      </details>
    </div>
  </details>;
}
