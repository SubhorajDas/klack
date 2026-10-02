'use client';
import { useEffect, useRef, useState } from 'react';
import { api, ApiError, errorMessage } from './api';
import { mergeMessages } from './messages';
import { getBrowserApiCache } from './api-cache';
import { useCachedData } from './use-cached-data';
import { channelPath, type Message, type MessagePage, type User } from './types';

export function useConversation(workspace: string, channel: string, parent?: string) {
  const path = `${channelPath(workspace, channel)}/messages`;
  const query = parent ? `&parent_message_id=${parent}` : '';
  const cacheKey = `conversation:${path}${parent ? `?parent=${parent}` : ''}`;
  const preview = useCachedData<MessagePage>(cacheKey);
  const cache = getBrowserApiCache();
  const cacheSession = useRef(cache?.session());
  const [messages, setMessages] = useState<Message[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [status, setStatus] = useState('Connecting');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [paging, setPaging] = useState(false);
  const buffer = useRef<Message[]>([]);
  const syncing = useRef(true);
  const alive = useRef(true);
  const readable = useRef(true);
  const historyEpoch = useRef(0);

  useEffect(() => {
    if (!loading && readable.current && alive.current && cacheSession.current !== undefined) {
      // Only keep the newest page as a navigation preview. Every visit still
      // fetches an authoritative snapshot and subscribes to live revisions.
      cache?.write(
        cacheKey,
        { messages: messages.slice(-50), next_before: null },
        cacheSession.current,
      );
    }
  }, [cache, cacheKey, loading, messages, next]);

  function merge(incoming: Message[]) {
    if (!alive.current || !readable.current) return;
    incoming = incoming.filter((m) => (m.parent_message_id || undefined) === parent);
    if (syncing.current) buffer.current.push(...incoming);
    setMessages((current) => mergeMessages(current, incoming));
  }

  useEffect(() => {
    let stopped = false;
    let denied = false;
    let socket: WebSocket;
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    let generation = 0;
    let snapshotSequence = 0;
    alive.current = true;
    readable.current = true;
    const previous = cache?.peek<MessagePage>(cacheKey);
    if (previous) {
      setMessages(previous.messages);
      setNext(null);
    }

    async function snapshot(version: number, live: boolean) {
      const sequence = ++snapshotSequence;
      try {
        const page = await api<MessagePage>(`${path}?limit=50${query}`);
        if (stopped || (live && version !== generation) || sequence !== snapshotSequence || denied)
          return;
        historyEpoch.current++;
        setMessages(mergeMessages(page.messages, buffer.current));
        buffer.current = [];
        syncing.current = !live;
        setNext(page.next_before);
        setError('');
        setLoading(false);
        if (live) {
          setStatus('Connected');
          attempts = 0;
        }
      } catch (failure) {
        if (stopped || (live && version !== generation) || sequence !== snapshotSequence) return;
        setLoading(false);
        setError(errorMessage(failure));
        if (failure instanceof ApiError && [403, 404].includes(failure.status)) revoke();
        else if (live) socket.close();
      }
    }
    function revoke() {
      denied = true;
      readable.current = false;
      cache?.remove(cacheKey);
      historyEpoch.current++;
      setMessages([]);
      setNext(null);
      setLoading(false);
      setStatus('Access unavailable');
      setError(
        'You no longer have access to this conversation. Browse channels to refresh your membership.',
      );
      socket?.close();
    }
    function connect() {
      if (stopped || denied) return;
      syncing.current = true;
      buffer.current = [];
      setStatus(attempts ? 'Reconnecting' : 'Connecting');
      const url = new URL('/api/v1/realtime', window.location.href);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(url, 'klack.realtime.v1');
      const version = ++generation;
      socket.onopen = () =>
        socket.send(
          JSON.stringify({
            type: 'subscribe',
            request_id: crypto.randomUUID(),
            workspace_id: workspace,
            channel_id: channel,
          }),
        );
      socket.onmessage = (event) => {
        if (stopped || version !== generation) return;
        let data;
        try {
          data = JSON.parse(event.data);
        } catch {
          return;
        }
        if (data.type === 'ping') socket.send(JSON.stringify({ type: 'pong' }));
        if (data.type === 'subscribed' && data.channel_id === channel) void snapshot(version, true);
        if (data.type === 'message.changed' && data.message.channel_id === channel) {
          merge([data.message]);
          window.dispatchEvent(new CustomEvent('klack:messages-changed', { detail: { channel } }));
        }
        if (
          data.type === 'subscription.revoked' ||
          (data.type === 'error' && data.code === 'channel_access_denied')
        )
          revoke();
        else if (data.type === 'error') {
          setError('Live updates are temporarily unavailable. Reconnecting…');
          socket.close();
        }
      };
      socket.onclose = () => {
        if (stopped || denied) return;
        generation++;
        setStatus('Reconnecting');
        timer = setTimeout(
          async () => {
            try {
              await api<User>('/auth/me');
            } catch (failure) {
              if (stopped) return;
              if (failure instanceof ApiError && [401, 403].includes(failure.status)) {
                revoke();
                return;
              }
            }
            connect();
          },
          Math.min(1000 * 2 ** attempts++, 30000),
        );
      };
    }
    connect();
    // Readable history remains available even if the socket cannot connect.
    void snapshot(generation, false);
    return () => {
      stopped = true;
      alive.current = false;
      clearTimeout(timer);
      socket?.close();
    };
    // The parent keys each conversation by channel, so state cannot cross channels.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, workspace, channel, parent, query]);

  async function older() {
    if (!next || paging || !readable.current) return;
    const epoch = historyEpoch.current;
    setPaging(true);
    try {
      const page = await api<MessagePage>(`${path}?before=${next}${query}`);
      if (alive.current && readable.current && epoch === historyEpoch.current) {
        merge(page.messages);
        setNext(page.next_before);
      }
    } catch (failure) {
      if (alive.current) setError(errorMessage(failure));
    } finally {
      if (alive.current) setPaging(false);
    }
  }
  return {
    messages: loading && preview ? preview.messages : messages,
    next: loading ? null : next,
    status,
    error,
    loading: loading && !preview,
    paging,
    older,
    merge,
    path,
  };
}
