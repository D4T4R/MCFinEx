/**
 * The shortlist screen, rendered against genuinely published data.
 *
 * The fixture is a slice of a real `mcfinex publish` run rather than invented
 * objects, so a field the app reads and the publisher stopped writing shows up
 * here as a failing render instead of as a blank space on someone's phone.
 */

import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react-native';

import { IdeasScreen } from '../screens/IdeasScreen';
import fixture from './fixtures/index.json';

function mountWithData(payload: unknown = fixture) {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => payload,
  }) as never;
  return render(<IdeasScreen onOpen={jest.fn()} watched={[]} />);
}

beforeEach(() => {
  jest.restoreAllMocks();
  require('@react-native-async-storage/async-storage').clear();
});

describe('IdeasScreen', () => {
  it('renders one card per pick in the opening tier', async () => {
    const expected = fixture.picks.filter((p) => p.tier === 'High conviction');
    mountWithData();
    await waitFor(() =>
      expect(screen.getAllByText(/\d+\/\d+ BUY/)).toHaveLength(expected.length),
    );
    for (const pick of expected) {
      expect(screen.getByText(pick.ticker)).toBeTruthy();
    }
  });

  it('shows how current the data is', async () => {
    mountWithData();
    await waitFor(() =>
      expect(screen.getByText(/Prices as of .* fundamentals to /)).toBeTruthy(),
    );
  });

  it('puts the disclaimer above the cards, not below them', async () => {
    // A reader who scrolls straight to the list must still have met it.
    mountWithData();
    await waitFor(() =>
      expect(screen.getByText(/Do your own research/)).toBeTruthy(),
    );
  });

  it('offers every tier with its count', async () => {
    mountWithData();
    await waitFor(() => expect(screen.getByText(/^High conviction \d+$/)).toBeTruthy());
    expect(screen.getByText(/^Re-rating \d+$/)).toBeTruthy();
    expect(screen.getByText(/^Watch \d+$/)).toBeTruthy();
  });

  it('describes a re-rating pick by its headroom, not its discount', async () => {
    // Its discount is negative by construction, so "vs entry" would present it
    // by the very bias that hid it from the headline score.
    mountWithData();
    await waitFor(() => expect(screen.getByText(/^Re-rating \d+$/)).toBeTruthy());
    fireEvent.press(screen.getByText(/^Re-rating \d+$/));

    // The fixture holds more than one re-rating name, so every card in the
    // tier must use the headroom wording, not just the first.
    await waitFor(() => expect(screen.getAllByText('Headroom').length).toBeGreaterThan(1));
    expect(screen.queryByText('vs entry')).toBeNull();
    expect(screen.getAllByText(/quality BUY/).length).toBeGreaterThan(0);
  });

  it('describes every other tier by its discount to the entry price', async () => {
    mountWithData();
    await waitFor(() => expect(screen.getAllByText('vs entry').length).toBeGreaterThan(0));
    expect(screen.queryByText('Headroom')).toBeNull();
  });

  it('surfaces data-quality flags on the card that carries them', async () => {
    mountWithData();
    await waitFor(() => expect(screen.getByText(/^High conviction \d+$/)).toBeTruthy());
    expect(screen.getByText(/upside implausibly large/)).toBeTruthy();
  });

  it('filters by ticker', async () => {
    mountWithData();
    await waitFor(() => expect(screen.getByText(/^Re-rating \d+$/)).toBeTruthy());
    fireEvent.press(screen.getByText(/^Re-rating \d+$/));
    await waitFor(() => expect(screen.getByText('MARATHON')).toBeTruthy());

    fireEvent.changeText(screen.getByPlaceholderText(/Filter by ticker/), 'MARATHON');
    await waitFor(() => expect(screen.getByText('MARATHON')).toBeTruthy());

    fireEvent.changeText(screen.getByPlaceholderText(/Filter by ticker/), 'ZZZZZZ');
    await waitFor(() => expect(screen.getByText(/Nothing in Re-rating matches/)).toBeTruthy());
  });

  it('says so plainly when it cannot load anything at all', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('offline')) as never;
    render(<IdeasScreen onOpen={jest.fn()} watched={[]} />);
    await waitFor(() => expect(screen.getByText('Nothing to show')).toBeTruthy());
  });

  it('never tells a reader to run a command they cannot run', async () => {
    // The web pages used to print `mcfinex prices`, which only the owner can
    // run and only on their own machine.
    mountWithData();
    await waitFor(() => expect(screen.getByText(/Prices as of/)).toBeTruthy());
    expect(screen.queryByText(/mcfinex /)).toBeNull();
  });
});
