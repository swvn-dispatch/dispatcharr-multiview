import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { AppProviders } from '@swvn-dispatch/dispatch-ui-kit';
import '@swvn-dispatch/dispatch-ui-kit/styles.css';
import { App } from './App.jsx';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AppProviders>
      <App />
    </AppProviders>
  </StrictMode>,
);
