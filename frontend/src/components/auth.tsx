'use client';
import { useState, type FormEvent } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowRight, Eye, EyeOff, MessageSquare, Check, Sparkles } from 'lucide-react';
import { api, errorMessage } from '@/lib/api';
import type { User } from '@/lib/types';
import { Alert, Avatar, Logo } from './ui';

export function Auth({ path, signedIn }: { path: string; signedIn: (user: User) => void }) {
  const register = path === '/register';
  const recovery = path === '/recover-password';
  const verify = path === '/verify-email';
  const [token] = useState(() =>
    typeof window !== 'undefined'
      ? new URLSearchParams(window.location.search).get('token') || ''
      : '',
  );
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const title = verify
    ? 'Verify your email'
    : recovery
      ? token
        ? 'Choose a new password'
        : 'Forgot your password?'
      : register
        ? 'Create your account'
        : 'Welcome back';
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setSuccess('');
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      if (verify) {
        await api('/auth/email-verification/complete', 'POST', { token }, false);
        setSuccess('Your email is verified. You’re ready to go.');
      } else if (recovery) {
        await api(
          `/auth/password-recovery/${token ? 'complete' : 'request'}`,
          'POST',
          token ? { token, new_password: form.get('password') } : { email: form.get('email') },
          false,
        );
        setSuccess(
          token
            ? 'Your password has been updated. Sign in with your new password.'
            : 'If an account exists for this email, a reset link is on its way. Check your inbox and spam folder.',
        );
      } else {
        const result = await api<{ user: User }>(
          `/auth/${register ? 'register' : 'login'}`,
          'POST',
          { email: form.get('email'), password: form.get('password') },
          false,
        );
        signedIn(result.user);
      }
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="auth-page">
      <section className="auth-main">
        <Link href="/" className="brand-link">
          <Logo />
        </Link>
        <div className="auth-form-wrap">
          <span className="eyebrow">A LITTLE LESS NOISE. A LOT MORE TOGETHER.</span>
          <h1>{title}</h1>
          <p className="auth-subtitle">
            {verify
              ? 'Confirm your email to finish setting up your account.'
              : recovery
                ? 'Let’s get you back to your team.'
                : register
                  ? 'A more connected team starts here.'
                  : 'Sign in to your workspace.'}
          </p>
          <form onSubmit={submit} className="form-stack">
            <Alert>{error}</Alert>
            {success && (
              <div className="success" role="status">
                <Check size={19} />
                <span>{success}</span>
              </div>
            )}
            {!verify && !(recovery && token) && (
              <label>
                Work email
                <input
                  type="email"
                  name="email"
                  placeholder="you@company.com"
                  autoComplete="email"
                  required
                  maxLength={320}
                />
              </label>
            )}
            {!verify && (!recovery || token) && (
              <label>
                Password
                <span className="password-input">
                  <input
                    name="password"
                    type={showPassword ? 'text' : 'password'}
                    placeholder={
                      register || recovery ? 'At least 12 characters' : 'Enter your password'
                    }
                    minLength={register || recovery ? 12 : 1}
                    maxLength={128}
                    autoComplete={register || recovery ? 'new-password' : 'current-password'}
                    required
                  />
                  <button
                    type="button"
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? <EyeOff size={19} /> : <Eye size={19} />}
                  </button>
                </span>
              </label>
            )}
            {!register && !recovery && !verify && (
              <div className="form-extra">
                <span>Stay connected to your team</span>
                <Link href="/recover-password">Forgot password?</Link>
              </div>
            )}
            {verify && !token && (
              <Alert>
                This verification link is incomplete. Request a new link in your account settings.
              </Alert>
            )}
            <button
              className="primary wide"
              disabled={
                busy || (verify && (!token || !!success)) || (recovery && !!token && !!success)
              }
            >
              {busy
                ? 'Please wait…'
                : verify
                  ? 'Verify email'
                  : recovery
                    ? token
                      ? 'Update password'
                      : 'Send reset link'
                    : register
                      ? 'Create account'
                      : 'Sign in'}
              {!busy && <ArrowRight size={18} />}
            </button>
          </form>
          <p className="auth-switch">
            {verify || recovery ? (
              <Link href="/login">
                <ArrowLeft size={15} /> Back to sign in
              </Link>
            ) : register ? (
              <>
                Already have an account? <Link href="/login">Sign in</Link>
              </>
            ) : (
              <>
                New here? <Link href="/register">Create an account</Link>
              </>
            )}
          </p>
          <div className="auth-footnote">
            <span className="tiny-dot" />A calmer place to do great work.
          </div>
        </div>
        <footer className="auth-footer">
          Klack <span>Built for working together.</span>
        </footer>
      </section>
      <aside className="auth-art">
        <div className="orb orb-one" />
        <div className="orb orb-two" />
        <div className="art-content">
          <span className="art-eyebrow">
            <Sparkles size={17} /> SPACE FOR YOUR BEST IDEAS
          </span>
          <h2>
            Your team,
            <br />
            in sync.
          </h2>
          <p>
            Conversations, projects, and people.
            <br />
            Together in one place.
          </p>
          <div className="sample-chat" aria-label="Illustration of a team conversation">
            <header>
              <span># design</span>
              <span className="sample-label">A little inspiration</span>
            </header>
            <div>
              <Avatar name="Priya" />
              <p>
                <strong>Priya</strong>
                <small>10:24 AM</small>
                <span>Here’s the latest design for review.</span>
              </p>
            </div>
            <div className="sample-attachment">
              <MessageSquare size={25} />
              <span>
                Room for great ideas<small>And the people behind them.</small>
              </span>
            </div>
            <div>
              <Avatar name="Daniel" />
              <p>
                <strong>Daniel</strong>
                <small>10:28 AM</small>
                <span>This is looking great! ✨</span>
              </p>
            </div>
            <div>
              <Avatar name="Maya" />
              <p>
                <strong>Maya</strong>
                <small>10:31 AM</small>
                <span>Excited to build this together. 💜</span>
              </p>
            </div>
          </div>
          <div className="sticky-note">
            Good ideas
            <br />
            bring people
            <br />
            together.
          </div>
          <span className="together">Better, together.</span>
        </div>
      </aside>
    </main>
  );
}
