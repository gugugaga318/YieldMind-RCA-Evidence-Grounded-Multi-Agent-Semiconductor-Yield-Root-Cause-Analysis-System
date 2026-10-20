// URLs contain only a Job locator; identity is always fetched from the Backend.
export function jobFromUrl(href: string): string | null {
  const id = new URL(href).searchParams.get("job");
  return id && /^[A-Za-z0-9_-]{1,128}$/.test(id) ? id : null;
}

export function jobUrl(href: string, jobId: string): string {
  const url = new URL(href);
  url.searchParams.set("job", jobId);
  return `${url.pathname}${url.search}${url.hash}`;
}
