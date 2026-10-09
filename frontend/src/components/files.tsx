'use client';

import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { Download, Paperclip, X } from 'lucide-react';
import { api, ApiError, errorMessage, refreshSession } from '@/lib/api';
import { channelPath, type Attachment, type Channel, type Message } from '@/lib/types';
import { Alert, Empty } from './ui';

export type QueuedFile = {
  key: string;
  filename: string;
  size: number;
  attachment?: Attachment;
  progress?: number;
  error?: string;
};
type Limits = { enabled: boolean; max_bytes: number; max_attachments: number };
export const fileSize = (size: number) =>
  size < 1024 * 1024
    ? `${Math.max(1, Math.ceil(size / 1024))} KB`
    : `${(size / 1024 / 1024).toFixed(1)} MB`;

function uploadBytes(
  path: string,
  file: File,
  progress: (value: number) => void,
  signal: AbortSignal,
) {
  return new Promise<Attachment>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', `/api/v1${path}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    const csrf = document.cookie
      .split('; ')
      .find((item) => item.startsWith('klack_csrf='))
      ?.slice(11);
    if (csrf) xhr.setRequestHeader('X-CSRF-Token', decodeURIComponent(csrf));
    xhr.timeout = 240000;
    xhr.upload.onprogress = (event) =>
      progress(Math.min(99, Math.round((event.loaded / file.size) * 100)));
    const abort = () => xhr.abort();
    signal.addEventListener('abort', abort, { once: true });
    xhr.onloadend = () => signal.removeEventListener('abort', abort);
    xhr.onerror = () =>
      reject(new ApiError(0, 'Upload interrupted. Retry when you are connected.'));
    xhr.ontimeout = () => reject(new ApiError(0, 'Upload timed out. Please retry.'));
    xhr.onabort = () => reject(new Error('Upload cancelled.'));
    xhr.onload = () => {
      let body;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = {};
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(xhr.status, body.detail || 'Upload failed. Please retry.'));
    };
    if (signal.aborted) reject(new Error('Upload cancelled.'));
    else xhr.send(file);
  });
}

export function FilePicker({
  path,
  entries,
  change,
  locked,
  registerInput,
  inline = false,
  added: onAdded = () => {},
  registerActions,
}: {
  path: string;
  entries: QueuedFile[];
  change: Dispatch<SetStateAction<QueuedFile[]>>;
  locked: boolean;
  registerInput: (accept: (files: File[]) => void) => void;
  inline?: boolean;
  added?: (entries: QueuedFile[]) => void;
  registerActions?: (actions: {
    retry: (key: string) => void;
    remove: (key: string) => void;
  }) => void;
}) {
  const [limits, setLimits] = useState<Limits | null>(null);
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const local = useRef(new Map<string, File>());
  const active = useRef(new Map<string, AbortController>());
  const alive = useRef(true);
  useEffect(() => {
    const keys = new Set(entries.map((entry) => entry.key));
    for (const key of local.current.keys()) if (!keys.has(key)) local.current.delete(key);
  }, [entries]);
  useEffect(() => {
    alive.current = true;
    api<Limits>(`${path}/limits`)
      .then(setLimits)
      .catch(() => setError('File uploads are unavailable.'));
    return () => {
      alive.current = false;
      active.current.forEach((controller) => controller.abort());
    };
  }, [path]);
  const update = (key: string, value: Partial<QueuedFile>) => {
    if (alive.current)
      change((rows) => rows.map((row) => (row.key === key ? { ...row, ...value } : row)));
  };
  async function upload(entry: QueuedFile) {
    const file = local.current.get(entry.key);
    if (!file) {
      update(entry.key, { error: 'Remove this item and select the file again.' });
      return;
    }
    const controller = new AbortController();
    active.current.set(entry.key, controller);
    update(entry.key, { error: undefined, progress: 0 });
    let reserved: Attachment | undefined;
    try {
      reserved = await api<Attachment>(path, 'POST', { filename: file.name, size: file.size });
      let attachment: Attachment;
      try {
        attachment = await uploadBytes(
          `${path}/${reserved.id}/content`,
          file,
          (progress) => update(entry.key, { progress }),
          controller.signal,
        );
      } catch (failure) {
        if (!(failure instanceof ApiError) || failure.status !== 401) throw failure;
        await refreshSession();
        attachment = await uploadBytes(
          `${path}/${reserved.id}/content`,
          file,
          (progress) => update(entry.key, { progress }),
          controller.signal,
        );
      }
      if (controller.signal.aborted || !alive.current) {
        void api(`${path}/${reserved.id}`, 'DELETE').catch(() => {});
      } else update(entry.key, { attachment, progress: 100 });
    } catch (failure) {
      if (reserved) void api(`${path}/${reserved.id}`, 'DELETE').catch(() => {});
      update(entry.key, { error: errorMessage(failure), progress: undefined });
    } finally {
      active.current.delete(entry.key);
    }
  }
  function accept(files: File[]) {
    if (locked || !limits?.enabled) return;
    setError('');
    if (files.length + entries.length > limits.max_attachments) {
      setError(`Choose up to ${limits.max_attachments} files per message.`);
      return;
    }
    if (files.some((file) => file.size === 0 || file.size > limits.max_bytes)) {
      setError(`Files must be nonempty and no larger than ${fileSize(limits.max_bytes)}.`);
      return;
    }
    const added = files.map((file) => {
      const entry = { key: crypto.randomUUID(), filename: file.name, size: file.size, progress: 0 };
      local.current.set(entry.key, file);
      return entry;
    });
    change((rows) => [...rows, ...added]);
    onAdded(added);
    // Sequential uploads avoid monopolizing server capacity while preserving individual retry.
    void (async () => {
      for (const entry of added)
        if (local.current.has(entry.key) && alive.current) await upload(entry);
    })();
  }
  useEffect(() => {
    registerInput(accept);
    registerActions?.({
      retry: (key) => {
        const entry = entries.find((e) => e.key === key);
        if (entry && !locked) void upload(entry);
      },
      remove: (key) => {
        if (locked) return;
        active.current.get(key)?.abort();
        local.current.delete(key);
        const entry = entries.find((e) => e.key === key);
        change((rows) => rows.filter((row) => row.key !== key));
        if (entry?.attachment) void api(`${path}/${entry.attachment.id}`, 'DELETE').catch(() => {});
      },
    });
  });
  return (
    <div className={inline ? 'inline-file-picker' : 'file-picker'}>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        aria-label="Choose attachments"
        disabled={locked || !limits?.enabled}
        onChange={(event) => {
          accept(Array.from(event.target.files || []));
          event.target.value = '';
        }}
      />
      <button
        type="button"
        className={inline ? 'icon-button' : 'text-button'}
        aria-label="Attach files"
        title="Attach files"
        disabled={locked || !limits?.enabled}
        onClick={() => input.current?.click()}
      >
        <Paperclip size={inline ? 20 : 16} /> {!inline && 'Attach files'}
      </button>
      {!inline && limits?.enabled && (
        <small className="muted">
          Up to {limits.max_attachments} files · {fileSize(limits.max_bytes)} each
        </small>
      )}
      <Alert>{error}</Alert>
      {!inline && entries.length > 0 && (
        <ul className="upload-queue" aria-label="Attachments to send">
          {entries.map((entry) => (
            <li key={entry.key}>
              <div>
                <strong>{entry.filename}</strong>
                <small>
                  {fileSize(entry.size)} ·{' '}
                  {entry.attachment
                    ? 'Ready'
                    : entry.error ||
                      (entry.progress === 99
                        ? 'Checking file…'
                        : `Uploading ${entry.progress || 0}%`)}
                </small>
                {!entry.attachment && !entry.error && (
                  <progress
                    max={100}
                    value={entry.progress || 0}
                    aria-label={`Uploading ${entry.filename}`}
                  />
                )}
              </div>
              {entry.error && (
                <button type="button" disabled={locked} onClick={() => void upload(entry)}>
                  Retry upload
                </button>
              )}
              <button
                type="button"
                className="icon-button"
                disabled={locked}
                aria-label={`Remove ${entry.filename}`}
                onClick={() => {
                  active.current.get(entry.key)?.abort();
                  local.current.delete(entry.key);
                  change((rows) => rows.filter((row) => row.key !== entry.key));
                  if (entry.attachment)
                    void api(`${path}/${entry.attachment.id}`, 'DELETE').catch(() => {});
                }}
              >
                <X size={16} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function AttachmentCards({ files, path }: { files: Attachment[]; path: string }) {
  const [error, setError] = useState('');
  async function download(file: Attachment) {
    setError('');
    try {
      let response = await fetch(`/api/v1${path}/${file.id}/content`, { cache: 'no-store' });
      if (response.status === 401) {
        await refreshSession();
        response = await fetch(`/api/v1${path}/${file.id}/content`, { cache: 'no-store' });
      }
      if (!response.ok)
        throw new Error('This file is no longer available or you no longer have access.');
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = file.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }
  return (
    <div className="attachment-list">
      {files.map((file) => (
        <div className="attachment-card" key={file.id}>
          {['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(file.content_type) && (
            <img
              loading="lazy"
              className="attachment-image"
              src={`/api/v1${path}/${file.id}/content?preview=true`}
              alt={file.filename}
            />
          )}
          <button
            type="button"
            onClick={() => void download(file)}
            aria-label={`Download ${file.filename}`}
          >
            <Download size={17} />
            <span>
              <strong>{file.filename}</strong>
              <small>{fileSize(file.size)}</small>
            </span>
          </button>
        </div>
      ))}
      <Alert>{error}</Alert>
    </div>
  );
}

export function MessageAttachments({ message }: { message: Message }) {
  if (message.deleted_at || !message.attachments?.length) return null;
  return (
    <AttachmentCards
      files={message.attachments}
      path={`${channelPath(message.workspace_id, message.channel_id)}/files`}
    />
  );
}

export function FilesPanel({ channel }: { channel: Channel }) {
  const path = `${channelPath(channel.workspace_id, channel.id)}/files`;
  const [files, setFiles] = useState<Attachment[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function load(before?: string) {
    setBusy(true);
    setError('');
    try {
      const page = await api<{ files: Attachment[]; next_before: string | null }>(
        `${path}${before ? `?before=${before}` : ''}`,
      );
      setFiles((old) =>
        before
          ? [...old, ...page.files.filter((file) => !old.some((item) => item.id === file.id))]
          : page.files,
      );
      setNext(page.next_before);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    void load();
  }, [path]);
  return (
    <section className="files-panel" aria-label="Shared files">
      <div className="button-row">
        <h2>Shared files</h2>
        <button disabled={busy} onClick={() => void load()}>
          Refresh files
        </button>
      </div>
      <Alert>{error}</Alert>
      {!busy && !error && !files.length && (
        <Empty title="No files shared yet">Attach a file to a message to share it here.</Empty>
      )}
      <AttachmentCards files={files} path={path} />
      {busy && <p role="status">Loading files…</p>}
      {next && (
        <button disabled={busy} onClick={() => void load(next)}>
          Load older files
        </button>
      )}
    </section>
  );
}
