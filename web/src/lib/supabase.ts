/**
 * Accounts and saved state, on the Supabase project the pipeline already uses.
 *
 * Nothing here gates the data. The screen is published as static JSON on a CDN
 * and is readable by anyone with the URL -- putting a login in front of this page
 * would be a door beside an open window. Signing in buys a watchlist and saved
 * screens that follow you between devices, and that is all it claims to do.
 *
 * The anon key belongs in the browser: it is a public identifier, not a secret,
 * and it grants nothing on its own. Every table reached from here is protected by
 * row-level security, so the policy is the whole protection -- see
 * web/supabase/preferences.sql. The service-role key must never appear in this
 * directory; it bypasses RLS entirely.
 *
 * Absent configuration is a supported state, not an error. The page is useful
 * without an account, so when the keys are missing the sign-in simply is not
 * offered.
 */

import { createClient, type SupabaseClient } from '@supabase/supabase-js';

const URL = import.meta.env.VITE_SUPABASE_URL;
const ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const authConfigured = Boolean(URL && ANON_KEY);

export const supabase: SupabaseClient | null = authConfigured
  ? createClient(URL as string, ANON_KEY as string, {
      auth: {
        // The OAuth redirect comes back with the session in the URL fragment.
        detectSessionInUrl: true,
        persistSession: true,
        autoRefreshToken: true,
      },
    })
  : null;

export async function signInWithGoogle(): Promise<void> {
  if (!supabase) return;
  await supabase.auth.signInWithOAuth({
    provider: 'google',
    // Back to wherever they were, not always the home page: somebody signing in
    // from a company page wants that company, not a reset.
    options: { redirectTo: window.location.href },
  });
}

export async function signOut(): Promise<void> {
  await supabase?.auth.signOut();
}
