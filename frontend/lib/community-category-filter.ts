import { communityPostCategories } from "./community-post-category";

export function isCommunityCategory(value: string) {
  return (communityPostCategories as readonly string[]).includes(value);
}

// Existing query values (team/search) survive; a category change leaves detail/page mode.
export function communityCategoryHref(href: string, category: string) {
  const url = new URL(href, "http://community.local");
  url.searchParams.delete("post");
  url.searchParams.delete("page");
  if (category) url.searchParams.set("category", category);
  else url.searchParams.delete("category");
  return url.pathname + (url.searchParams.size ? `?${url.searchParams}` : "");
}

export function matchesCommunityCategory(post: { category: string }, category: string) {
  return !category || post.category === category;
}
