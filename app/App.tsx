/**
 * MCFinEx on a phone.
 *
 * Reads the static JSON published by `mcfinex publish`; there is no server and
 * no account. The watchlist is held here rather than in each screen so the star
 * on a card and the star on the detail page cannot disagree.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import { Text } from 'react-native';
import { NavigationContainer, DefaultTheme, DarkTheme } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AlertsScreen } from './src/screens/AlertsScreen';
import { CompanyScreen } from './src/screens/CompanyScreen';
import { IdeasScreen } from './src/screens/IdeasScreen';
import { useTheme } from './src/theme';
import { Pick } from './src/types';
import { setWatched, watchlist } from './src/watchlist';

export type RootStackParamList = {
  Ideas: undefined;
  Company: { pick: Pick };
  Alerts: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();

export default function App() {
  const theme = useTheme();
  const [watched, setWatchedIds] = useState<string[]>([]);

  useEffect(() => {
    watchlist().then(setWatchedIds);
  }, []);

  const toggleWatch = useCallback(
    async (id: string) => setWatchedIds(await setWatched(id, !watched.includes(id))),
    [watched],
  );

  const navTheme = {
    ...(theme.dark ? DarkTheme : DefaultTheme),
    colors: {
      ...(theme.dark ? DarkTheme : DefaultTheme).colors,
      background: theme.bg,
      card: theme.card,
      text: theme.text,
      border: theme.border,
      primary: theme.accent,
    },
  };

  return (
    <SafeAreaProvider>
      <StatusBar style={theme.dark ? 'light' : 'dark'} />
      <NavigationContainer theme={navTheme}>
        <Stack.Navigator>
          <Stack.Screen
            name="Ideas"
            options={({ navigation }) => ({
              title: 'MCFinEx',
              headerRight: () => (
                <Text
                  accessibilityRole="button"
                  onPress={() => navigation.navigate('Alerts')}
                  style={{ color: theme.accent, fontSize: 15 }}
                >
                  Alerts
                </Text>
              ),
            })}
          >
            {({ navigation }) => (
              <IdeasScreen
                watched={watched}
                onOpen={(pick) => navigation.navigate('Company', { pick })}
              />
            )}
          </Stack.Screen>

          <Stack.Screen
            name="Company"
            options={({ route }) => ({ title: route.params.pick.ticker })}
          >
            {({ route }) => (
              <CompanyScreen
                pick={route.params.pick}
                watched={watched.includes(route.params.pick.id)}
                onToggleWatch={toggleWatch}
              />
            )}
          </Stack.Screen>

          <Stack.Screen name="Alerts" options={{ title: 'Alerts & disclaimer' }}>
            {() => <AlertsScreen watched={watched} />}
          </Stack.Screen>
        </Stack.Navigator>
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
