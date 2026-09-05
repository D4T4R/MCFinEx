/**
 * The alerts and disclaimer screen.
 *
 * Two things here are not cosmetic. The disclaimer must be present whatever the
 * network did -- the screen is titled for it, and it is the only place the full
 * wording appears. And the push switches must not pretend to work in a build
 * with no Firebase, which is every build until google-services.json exists.
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react-native';

import { AlertsScreen } from '../screens/AlertsScreen';
import { FULL_DISCLAIMER } from '../disclaimer';
import fixture from './fixtures/index.json';

beforeEach(() => {
  jest.restoreAllMocks();
  require('@react-native-async-storage/async-storage').clear();
});

describe('AlertsScreen', () => {
  it('shows the full disclaimer from the payload when it has one', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => fixture,
    }) as never;

    render(<AlertsScreen watched={[]} />);
    await waitFor(() => expect(screen.getByText(/not investment advice/i)).toBeTruthy());
  });

  it('still shows it with no network and nothing cached', async () => {
    // The regression this guards: the text used to arrive through a prop that
    // was never passed, so the screen rendered its own title and no disclaimer.
    global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as never;

    render(<AlertsScreen watched={[]} />);
    await waitFor(() => expect(screen.getByText(/not investment advice/i)).toBeTruthy());
    expect(screen.getByText(/SEBI-registered/)).toBeTruthy();
  });

  it('renders the disclaimer as text, not as raw markdown', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as never;
    render(<AlertsScreen watched={[]} />);
    await waitFor(() => expect(screen.getByText(/not investment advice/i)).toBeTruthy());
    expect(screen.queryByText(/\*\*/)).toBeNull();
  });

  it('says plainly that push does nothing without Firebase', async () => {
    // jest.setup.js makes the Firebase require throw, which is exactly what a
    // build without google-services.json does.
    global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as never;
    render(<AlertsScreen watched={[]} />);
    await waitFor(() =>
      expect(screen.getByText(/Push is not configured in this build/)).toBeTruthy(),
    );
  });

  it('offers every topic the notify job publishes to', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as never;
    render(<AlertsScreen watched={[]} />);
    await waitFor(() => expect(screen.getByText('Entry price reached')).toBeTruthy());
    expect(screen.getByText('New high-conviction name')).toBeTruthy();
    expect(screen.getByText('Daily pick')).toBeTruthy();
  });

  it('shows watched companies by ticker, not by file id', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as never;
    render(<AlertsScreen watched={['M_M']} />);
    // The id is the filename; a reader recognises M&M.
    await waitFor(() => expect(screen.getByText(/M&M/)).toBeTruthy());
  });

  it('explains the empty watchlist rather than showing a blank panel', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as never;
    render(<AlertsScreen watched={[]} />);
    await waitFor(() => expect(screen.getByText(/Nothing watched yet/)).toBeTruthy());
  });

  it('keeps the compiled fallback identical to the published wording', () => {
    // tests/test_disclaimer.py holds the other half of this: that the compiled
    // copy matches disclaimer.py.
    expect(fixture.disclaimer_full).toBe(FULL_DISCLAIMER);
  });
});
