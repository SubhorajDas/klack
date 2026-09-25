import { describe, expect, it } from 'vitest';
import { memberName, type Membership, type User } from './types';

const user: User = {
  id: 'self',
  email: 'lolkumar@example.com',
  email_verified: true,
  created_at: '',
};
const members: Membership[] = [
  {
    user_id: '6417-b69a',
    workspace_id: 'workspace',
    role: 'member',
    joined_at: '',
    display_name: 'subhorajdas084',
  },
];

describe('member names', () => {
  it('uses the current user email username', () => {
    expect(memberName('self', user, members)).toBe('lolkumar');
  });
  it('resolves other message authors from the workspace roster', () => {
    expect(memberName('6417-b69a', user, members)).toBe('subhorajdas084');
  });
  it('resolves compact IDs from direct message keys', () => {
    expect(memberName('6417b69a', user, members)).toBe('subhorajdas084');
  });
  it('keeps a fallback for unavailable members', () => {
    expect(memberName('unknown-id', user, members)).toBe('Member unknown-');
  });
});
