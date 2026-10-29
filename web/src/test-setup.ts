// SPDX-License-Identifier: MIT OR Apache-2.0
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
Element.prototype.scrollIntoView = vi.fn();
afterEach(cleanup);
