export const jsonResponse = (body: unknown, init: ResponseInit = {}) => {
  const headers = new Headers(init.headers ?? {});
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  return new Response(JSON.stringify(body), {
    ...init,
    headers,
  });
};

export const emptyResponse = (init: ResponseInit = {}) =>
  new Response(null, init);

export const parseRequestBody = async <T>(
  request: Request,
): Promise<T | undefined> => {
  if (request.method === "GET" || request.method === "HEAD") {
    return undefined;
  }

  try {
    const text = await request.clone().text();
    if (!text) {
      return undefined;
    }
    return JSON.parse(text) as T;
  } catch {
    return undefined;
  }
};
