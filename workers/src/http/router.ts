export type RouteHandler = () => Promise<Response>;

export async function dispatchRoute(
  path: string,
  routes: Partial<Record<string, RouteHandler>>,
): Promise<Response | null> {
  const handler = routes[path];
  if (!handler) {
    return null;
  }
  return handler();
}
