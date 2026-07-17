import { useState } from 'react';
import { LoginScreen } from '@swvn-dispatch/dispatch-ui-kit';
import { Dashboard } from './components/Dashboard.jsx';
import { isAuthenticated, login, logout } from './api.js';
import logoUrl from '/logo.png';

export function App() {
  const [authed, setAuthed] = useState(isAuthenticated());

  if (!authed) {
    return (
      <LoginScreen
        logoUrl={logoUrl}
        appName="Multiview"
        description="Sign in with your Dispatcharr credentials. The account must have permission to modify plugin settings."
        onLogin={login}
        onLoggedIn={() => setAuthed(true)}
      />
    );
  }

  return (
    <Dashboard
      onLoggedOut={() => {
        logout();
        setAuthed(false);
      }}
    />
  );
}
