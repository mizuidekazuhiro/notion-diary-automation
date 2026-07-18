export type RouteHandler = () => Promise<Response>;

const ROUTE_ALIASES: Readonly<Record<string, string>> = {
  "/execute/api/study/session": "/execute/api/daily_log/generate_diary",
};

export function resolveRoutePath(path: string): string {
  return ROUTE_ALIASES[path] ?? path;
}

export async function dispatchRoute(
  path: string,
  routes: Partial<Record<string, RouteHandler>>,
): Promise<Response | null> {
  const handler = routes[resolveRoutePath(path)];
  if (!handler) {
    return null;
  }
  return handler();
}
