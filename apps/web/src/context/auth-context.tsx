'use client';

import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import {
  AuthSuccessResponse,
  LoginRequest,
  OrganizationSchema,
  OrganizationSelectionRequiredResponse,
  RegisterRequest,
  UserSchema,
} from '@/types/api';
import {
  getCurrentUser,
  getAccessToken,
  loginUser,
  logoutAllUser,
  logoutUser,
  performSilentRefresh,
  registerUser,
  setAccessToken,
  setOnSessionExpired,
} from '@/lib/api-client';

type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'unauthenticated';

interface AuthContextType {
  user: UserSchema | null;
  organization: OrganizationSchema | null;
  status: AuthStatus;
  login: (payload: LoginRequest) => Promise<AuthSuccessResponse | OrganizationSelectionRequiredResponse>;
  register: (payload: RegisterRequest) => Promise<AuthSuccessResponse>;
  logout: () => Promise<void>;
  logoutAll: () => Promise<void>;
  refreshSession: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AUTH_CHANNEL_NAME = 'email_discovery_auth_channel';
export const LOGOUT_STORAGE_KEY = 'email_discovery_logout_marker';

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<UserSchema | null>(null);
  const [organization, setOrganization] = useState<OrganizationSchema | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');

  const clearAuthState = useCallback(() => {
    setAccessToken(null);
    setUser(null);
    setOrganization(null);
    setStatus('unauthenticated');
  }, []);

  const broadcastLogout = useCallback(() => {
    // 1. BroadcastChannel when supported
    if (typeof window !== 'undefined' && 'BroadcastChannel' in window) {
      try {
        const channel = new BroadcastChannel(AUTH_CHANNEL_NAME);
        channel.postMessage({ type: 'LOGOUT', timestamp: Date.now() });
        channel.close();
      } catch {
        // Fallback for BroadcastChannel instantiation failure
      }
    }

    // 2. Storage event marker fallback (stores non-sensitive timestamp marker only)
    if (typeof window !== 'undefined' && window.localStorage) {
      try {
        window.localStorage.setItem(LOGOUT_STORAGE_KEY, Date.now().toString());
      } catch {
        // Fallback for quota or restricted storage
      }
    }
  }, []);

  const fetchAndSetProfile = useCallback(async () => {
    const profile = await getCurrentUser();
    setUser({
      id: profile.id,
      email: profile.email,
      display_name: profile.display_name,
      status: profile.status,
    });
    setOrganization({
      id: profile.organization_id,
      name: profile.organization_name,
      slug: profile.organization_slug,
      role: profile.role,
    });
    setStatus('authenticated');
  }, []);

  const refreshSession = useCallback(async () => {
    try {
      setStatus('loading');
      await performSilentRefresh();
      await fetchAndSetProfile();
    } catch {
      clearAuthState();
    }
  }, [clearAuthState, fetchAndSetProfile]);

  // Initial silent refresh on app mount and multi-tab logout event listeners
  useEffect(() => {
    let isMounted = true;

    const handleLogoutEvent = () => {
      if (isMounted) {
        clearAuthState();
      }
    };

    setOnSessionExpired(() => {
      handleLogoutEvent();
    });

    // 1. BroadcastChannel listener
    let channel: BroadcastChannel | null = null;
    if (typeof window !== 'undefined' && 'BroadcastChannel' in window) {
      try {
        channel = new BroadcastChannel(AUTH_CHANNEL_NAME);
        channel.onmessage = (event) => {
          if (event.data?.type === 'LOGOUT') {
            handleLogoutEvent();
          }
        };
      } catch {
        // Fallback
      }
    }

    // 2. Storage event listener fallback
    const onStorageChange = (event: StorageEvent) => {
      if (event.key === LOGOUT_STORAGE_KEY && event.newValue) {
        handleLogoutEvent();
      }
    };

    if (typeof window !== 'undefined') {
      window.addEventListener('storage', onStorageChange);
    }

    refreshSession();

    return () => {
      isMounted = false;
      setOnSessionExpired(null);
      if (channel) {
        channel.close();
      }
      if (typeof window !== 'undefined') {
        window.removeEventListener('storage', onStorageChange);
      }
    };
  }, [refreshSession, clearAuthState]);

  const login = async (payload: LoginRequest): Promise<AuthSuccessResponse | OrganizationSelectionRequiredResponse> => {
    const res = await loginUser(payload);
    if ('organization_selection_required' in res && res.organization_selection_required) {
      return res;
    }
    const successRes = res as AuthSuccessResponse;
    await fetchAndSetProfile();
    return successRes;
  };

  const register = async (payload: RegisterRequest): Promise<AuthSuccessResponse> => {
    const res = await registerUser(payload);
    await fetchAndSetProfile();
    return res;
  };

  const logout = async (): Promise<void> => {
    try {
      await logoutUser();
    } finally {
      clearAuthState();
      broadcastLogout();
    }
  };

  const logoutAll = async (): Promise<void> => {
    try {
      await logoutAllUser();
    } finally {
      clearAuthState();
      broadcastLogout();
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        organization,
        status,
        login,
        register,
        logout,
        logoutAll,
        refreshSession,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
