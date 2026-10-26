import { useRef, useState } from "react";
import { parseRequirementsFile, parseRequirementsText } from "../api/requirements";
import type { ParseResponse } from "../types/requirements";

type Props = {
  onParsed: (response: ParseResponse) => void;
  onParseError: (error: unknown) => void;
  onSourceChange: () => void;
  busy: boolean;
};

const pythonVersions = ["3.10", "3.11", "3.12", "3.13", "3.14"];
const supportedPythonVersions = new Set(pythonVersions);

export function RequirementsInput({ onParsed, onParseError, onSourceChange, busy }: Props) {
  const [localBusy, setLocalBusy] = useState(false);
  const [text, setText] = useState("");
  const [pythonVersion, setPythonVersion] = useState("3.12");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function acceptFile(candidate?: File) {
    if (!candidate) return;
    onSourceChange();
    setText("");
    if (candidate.name.toLowerCase() !== "requirements.txt") {
      setFile(null);
      onParseError(new Error("Choose a file named requirements.txt."));
      return;
    }
    setFile(candidate);
  }

  async function parse() {
    onSourceChange();
    setLocalBusy(true);
    try {
      const parsed = file
        ? await parseRequirementsFile(file, pythonVersion)
        : await parseRequirementsText(text, pythonVersion);
      onParsed(parsed);
    } catch (error) {
      onParseError(error);
    } finally {
      setLocalBusy(false);
    }
  }

  return (
    <section className="input-card" aria-labelledby="input-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">START WITH YOUR DEPENDENCIES</p>
          <h2 id="input-title">Bring a requirements.txt</h2>
        </div>
        <label className="python-select">
          <span>Python target</span>
          <select value={pythonVersion} onChange={(event) => { setPythonVersion(event.target.value); onSourceChange(); }}>
            {pythonVersions.map((version) => <option key={version} value={version} disabled={!supportedPythonVersions.has(version)}>
              {supportedPythonVersions.has(version) ? version : `${version} (unsupported in this release)`}
            </option>)}
          </select>
        </label>
      </div>

      <button type="button" className={`dropzone ${dragging ? "dropzone--active" : ""}`} onClick={() => inputRef.current?.click()}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => { event.preventDefault(); setDragging(false); acceptFile(event.dataTransfer.files[0]); }}>
        <span className="drop-icon" aria-hidden="true">↥</span>
        <span className="drop-title">{file ? file.name : "Drop requirements.txt here"}</span>
        <span className="drop-note">or browse from your computer · up to 64 KB</span>
      </button>
      <input ref={inputRef} className="visually-hidden" type="file" accept=".txt,text/plain" aria-label="Choose requirements.txt file"
        onChange={(event) => acceptFile(event.target.files?.[0])} />

      <div className="divider"><span>OR PASTE CONTENTS</span></div>
      <textarea aria-label="Paste requirements.txt contents" value={text} rows={6} disabled={Boolean(file)}
        placeholder={'numpy>=1.26,<2\npandas==2.2.3\nrequests>=2.31'}
        onChange={(event) => { setText(event.target.value); setFile(null); onSourceChange(); }} />
      {file && <button type="button" className="text-button" onClick={() => { setFile(null); onSourceChange(); }}>Clear selected file to paste instead</button>}

      <div className="input-actions">
        <p>Bugsy parses first. Unsupported lines stop analysis and show where to fix the source. Sandbox image availability is unverified until the actual runtime is reported.</p>
        <button type="button" className="primary-button" disabled={busy || localBusy || (!file && !text.trim())} onClick={() => void parse()}>
          {busy || localBusy ? "Parsing…" : "Parse requirements"}<span aria-hidden="true">→</span>
        </button>
      </div>
    </section>
  );
}
