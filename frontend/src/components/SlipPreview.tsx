import { useEffect, useRef, useState } from "react";
import { api, download, type Kind } from "../api";

export interface SlipRef {
  kind: Kind;
  store: string;
  date: string;
  downloadable: boolean;
}

interface Props {
  slip: SlipRef | null;
  kindName: (k: Kind) => string;
  onClose: () => void;
}

export function SlipPreview({ slip, kindName, onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<"" | "ok" | "fail">("");

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (!slip) {
      if (d.open) d.close();
      return;
    }
    setText(null);
    setError(null);
    if (!d.open) d.showModal();
    let alive = true;
    api
      .slipText(slip.kind, slip.store, slip.date)
      .then((t) => alive && setText(t))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [slip]);

  const copy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied("ok");
    } catch {
      setCopied("fail");
    }
    setTimeout(() => setCopied(""), 1200);
  };

  const name = slip ? `${slip.store}_${slip.date}_${slip.kind}.txt` : "";
  const lines = text?.replace(/\n$/, "").split("\n") ?? [];

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && onClose()}
      aria-label={name}
    >
      <div className="dlg-head">
        <strong>
          {name}
          {slip && <span className="hint"> · {kindName(slip.kind)}</span>}
        </strong>
        <div className="dlg-actions">
          <button type="button" onClick={copy} disabled={!text || !slip?.downloadable}>
            {copied === "ok" ? "Copied" : copied === "fail" ? "Copy failed" : "Copy"}
          </button>
          <button
            type="button"
            className="primary"
            disabled={!slip?.downloadable}
            title={slip && !slip.downloadable ? "Today's slips can be downloaded from tomorrow" : undefined}
            onClick={() => slip && download(api.slipUrl(slip.kind, slip.store, slip.date, true))}
          >
            ↓ Download
          </button>
          <button type="button" className="ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
      </div>
      <pre className="slip">
        {error ? (
          <span className="err">{error}</span>
        ) : text === null ? (
          "Loading…"
        ) : (
          lines.map((line, i) => (
            <span className="ln" key={i}>
              {line}
            </span>
          ))
        )}
      </pre>
    </dialog>
  );
}
