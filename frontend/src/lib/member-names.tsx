'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, ApiError } from './api';
import { useCachedApiData } from './use-cached-data';
import { memberName, workspacePath, type Membership, type User } from './types';

const MemberNames = createContext<Membership[]>([]);

export function MemberNamesProvider({
  workspace,
  children,
}: {
  workspace: string;
  children: ReactNode;
}) {
  const [members, setMembers] = useState<Membership[]>([]);
  const cached = useCachedApiData<{ memberships: Membership[] }>(
    `${workspacePath(workspace)}/memberships`,
  );
  useEffect(() => {
    if (!workspace) return;
    let stopped = false;
    async function refresh() {
      try {
        const data = await api<{ memberships: Membership[] }>(
          `${workspacePath(workspace)}/memberships`,
        );
        if (!stopped) setMembers(data.memberships);
      } catch (failure) {
        if (!stopped && failure instanceof ApiError && [401, 403, 404].includes(failure.status))
          setMembers([]);
        // Keep the last known names during a temporary connection failure.
      }
    }
    void refresh();
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    const timer = setInterval(onVisible, 15000);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      stopped = true;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [workspace]);
  return (
    <MemberNames.Provider value={cached?.memberships ?? members}>{children}</MemberNames.Provider>
  );
}

export function useMemberName() {
  const members = useContext(MemberNames);
  return (id: string, user: User) => memberName(id, user, members);
}
