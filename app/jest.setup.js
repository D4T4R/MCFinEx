/**
 * Test doubles for the two things the app cannot have in a test process:
 * device storage and Firebase.
 *
 * Firebase is mocked to *fail* on require, which is the state that matters --
 * it is what a build without google-services.json does, and the app is
 * supposed to keep working in exactly that case.
 */

// React 19 refuses to batch state updates outside act() unless it is told it
// is in a test environment, and every async load in the app sets state.
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// The library waits one second for an async assertion. That is ample once
// Babel has a warm transform cache and not always enough without one, so the
// suite passed here and failed the first time it ran anywhere else -- and a
// suite that only fails on a cold cache is a suite that only fails in CI.
// Reproduce the old behaviour with `npx jest --no-cache`.
require('@testing-library/react-native').configure({ asyncUtilTimeout: 5000 });

jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);

jest.mock('@react-native-firebase/messaging', () => {
  throw new Error('native module not available');
});
