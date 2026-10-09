'use client';
import { useEffect, useRef, type ReactNode } from 'react';
import { X, MessageSquare, LoaderCircle } from 'lucide-react';
import { useOnlineStatus } from '@/lib/presence';

export function Logo({ name = 'Klack' }: { name?: string }) {
  return (
    <div className="brand">
      <span className="logo">K</span>
      <strong>{name}</strong>
    </div>
  );
}
export function Avatar({ name, small = false }: { name: string; small?: boolean }) {
  const color = [...name].reduce((value, character) => value + character.charCodeAt(0), 0) % 5;
  return (
    <span className={`avatar color-${color} ${small ? 'small' : ''}`} aria-hidden="true">
      {name.slice(0, 2).toUpperCase()}
    </span>
  );
}
export function UserAvatar({
  userId,
  name,
  small = false,
}: {
  userId: string;
  name: string;
  small?: boolean;
}) {
  const status = useOnlineStatus(userId);
  return (
    <span className="user-avatar">
      <Avatar name={name} small={small} />
      {status !== 'unknown' && (
        <span
          className={`presence-dot ${status}`}
          role="img"
          aria-label={`${name} is ${status}`}
          title={`${name} is ${status}`}
        />
      )}
    </span>
  );
}

export function OnlineLabel({ userId }: { userId: string }) {
  const status = useOnlineStatus(userId);
  return (
    <span className={`online-label ${status}`}>
      {status === 'unknown' ? 'Status unavailable' : status === 'online' ? 'Online' : 'Offline'}
    </span>
  );
}
export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={22} />
      {label}
    </div>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <MessageSquare size={30} />
      </span>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function Alert({ children }: { children?: ReactNode }) {
  return children ? (
    <div className="alert" role="alert">
      {children}
    </div>
  ) : null;
}
export function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      onCancel={close}
      onClick={(event) => {
        if (event.target === ref.current) close();
      }}
      aria-label={title}
    >
      <section className="modal">
        <header>
          <h2>{title}</h2>
          <button className="icon-button" aria-label="Close dialog" onClick={close}>
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </dialog>
  );
}
