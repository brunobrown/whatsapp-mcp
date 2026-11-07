import { setupServer } from "msw/node";

/** One MSW server for the suite; each test adds the handlers it needs. */
export const server = setupServer();
