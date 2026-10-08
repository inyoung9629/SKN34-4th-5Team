"use client";

import { useCallback, useRef, useState } from "react";
import { createCourseState, reduceCourse, type CourseAction } from "@/lib/course-state";
import type { RouteDraftData } from "@/lib/route-draft";

export function useCourseState(initial: () => RouteDraftData) {
  const [state, setState] = useState(() => createCourseState(initial()));
  const ref = useRef(state);
  const dispatch = useCallback((action: CourseAction) => {
    const before = ref.current;
    const next = reduceCourse(before, action);
    if (next !== before) { ref.current = next; setState(next); }
    return next;
  }, []);
  return { state, ref, dispatch };
}
