import { describe, expect, it } from 'vitest';
import { mergeMessages } from './messages';
import type { Message } from './types';
const base: Message = {
  id: 'one',
  workspace_id: 'workspace',
  channel_id: 'channel',
  author_user_id: 'author',
  body: 'hello',
  created_at: '2026-09-24T09:00:00Z',
  edited_at: null,
  deleted_at: null,
  client_message_id: 'client',
  revision: 1,
};
describe('revision reconciliation', () => {
  it('retains a newer socket edit when stale history arrives', () => {
    const edit = { ...base, body: 'updated', revision: 2 };
    expect(mergeMessages([edit], [base])).toEqual([edit]);
    expect(mergeMessages([base], [edit])).toEqual([edit]);
  });
  it('never restores deleted content with an older edit or duplicate', () => {
    const deleted = { ...base, body: null, deleted_at: '2026-09-24T10:00:00Z', revision: 3 };
    expect(mergeMessages([deleted], [base, { ...base, revision: 2 }, deleted])).toEqual([deleted]);
  });
  it('deduplicates a REST response and its socket echo and orders history', () => {
    const older = { ...base, id: 'older', created_at: '2026-09-23T09:00:00Z' };
    expect(mergeMessages([base], [base, older])).toEqual([older, base]);
  });
});
