export type TopicRecord = {
  id: string;
  title: string;
  category: string;
  status: string;
  ranking_score: number | null;
  source_count: number;
  created_at: string;
  updated_at: string;
};

export async function request<T>(
  path: string,
  accessToken: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(`/api/backend${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${accessToken}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      ) {
        message = body.detail;
      } else if (
        typeof body === "object" &&
        body !== null &&
        "error" in body &&
        typeof body.error === "object" &&
        body.error !== null &&
        "message" in body.error &&
        typeof body.error.message === "string"
      ) {
        message = body.error.message;
      }
    } catch (error) {
      if (!(error instanceof SyntaxError)) {
        throw error;
      }
    }
    throw new Error(message);
  }

  return (await response.json()) as T;
}

export function listTopics(accessToken: string): Promise<TopicRecord[]> {
  return request<TopicRecord[]>("/topics", accessToken);
}

export function decideTopic(
  accessToken: string,
  topicId: string,
  input: { decision: "approve" | "reject"; note: string },
): Promise<TopicRecord> {
  return request<TopicRecord>(`/topics/${topicId}/decision`, accessToken, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createTopic(
  accessToken: string,
  input: { title: string; category: string },
): Promise<TopicRecord> {
  return request<TopicRecord>("/topics", accessToken, {
    method: "POST",
    body: JSON.stringify(input),
  });
}
