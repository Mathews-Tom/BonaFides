// SPDX-License-Identifier: MIT OR Apache-2.0
import { createRoot } from 'react-dom/client';
import { App } from './App';
import './styles.css';
const root = document.getElementById('root');
if (!root) throw new Error('Application root is missing');
createRoot(root).render(<App />);
