import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

function load(file, dependencies = {}) {
  const source = readFileSync(new URL("../" + file, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } });
  const loaded = { exports: {} };
  new Function("module", "exports", "require", outputText)(loaded, loaded.exports, name => {
    if (!(name in dependencies)) throw new Error("Unexpected dependency: " + name);
    return dependencies[name];
  });
  return loaded.exports;
}

const teams = load("lib/team-community.ts");
const { ApiError } = load("lib/api/client.ts");
const paths = load("lib/member-return-path.ts");
function mockGlobal(t, name, value) {
  const original = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { value, writable: true, configurable: true });
  t.after(() => {
    if (original) Object.defineProperty(globalThis, name, original);
    else delete globalThis[name];
  });
}
function flatten(tree) {
  if (Array.isArray(tree)) return tree.flatMap(flatten);
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...flatten(tree.props?.children)];
}

function screen({ hydrated = true, failure = null } = {}) {
  const state = [], requests = [], redirects = [], focused = [];
  let cursor = 0;
  const jsx = (type, props) => ({ type, props });
  const Signup = load("app/signup/page.tsx", {
    "react/jsx-runtime": { jsx, jsxs: jsx },
    react: {
      useState: initial => {
        const index = cursor++;
        if (!(index in state)) state[index] = initial;
        return [state[index], value => { state[index] = typeof value === "function" ? value(state[index]) : value; }];
      },
      useRef: () => ({ current: null }), useEffect: () => {},
    },
    "next/navigation": { useRouter: () => ({ push: value => redirects.push(value) }) },
    "@/lib/member-return-path": paths,
    "@/components/member-auth-switch-link": { MemberAuthSwitchLink: "auth-switch" },
    "@/components/auth-dialog": { AuthDialog: "dialog" },
    "@/components/auth-hydration": { useAuthHydrated: () => hydrated },
    "@/lib/api/auth": { signUp: async body => { requests.push(body); if (failure) throw failure; } },
    "@/lib/api/client": { ApiError },
    "@/lib/team-community": teams,
  }).default;
  const render = () => { cursor = 0; return flatten(Signup()); };
  const fill = (teamCode = "") => {
    const values = { username: "signupfan", password: "RiverStone742!Q", passwordConfirm: "RiverStone742!Q", name: "야구팬", birthDate: "2000-01-01", gender: "F", email: "fan@example.test", teamCode };
    for (const [name, value] of Object.entries(values)) {
      render().find(node => node.props?.name === name).props.onChange({ target: { value } });
    }
    render().find(node => node.type === "input" && node.props.type === "checkbox" && node.props.ref).props.onChange({ target: { checked: true } });
  };
  const submit = async () => {
    const form = render().find(node => node.type === "form");
    await form.props.onSubmit({ preventDefault() {}, currentTarget: { querySelector: id => ({ focus: () => focused.push(id) }) } });
  };
  return { render, fill, submit, requests, redirects, focused };
}

test("signup offers optional team and stadium labels using the shared list", () => {
  const form = screen();
  const select = form.render().find(node => node.props?.name === "teamCode");
  assert.equal(select.props.required, undefined);
  assert.equal(select.props.value, "");
  const options = flatten(select).filter(node => node.type === "option");
  assert.equal(options.length, 11);
  assert.equal(options[0].props.value, "");
  assert.deepEqual(options.slice(1).map(node => node.props.value), teams.teamBoards.map(team => team.code));
  assert.deepEqual(options.slice(1).map(node => node.props.children.join("")), teams.teamBoards.map(team => `${team.name} · ${team.stadium}`));
  assert.equal(screen({ hydrated: false }).render().find(node => node.props?.name === "teamCode").props.disabled, true);
});

test("signup submits team selection or empty value and preserves member return path", async (t) => {
  mockGlobal(t, "window", { location: { search: "?next=%2Fcommunity%2Fmembers%2F21%3Ftab%3Dcomments%26page%3D2" } });
  for (const teamCode of ["LG", "OB", ""]) {
    const form = screen();
    form.fill(teamCode);
    await form.submit();
    assert.equal(form.requests[0].team_code, teamCode);
    const url = new URL(form.redirects[0], "https://local.invalid");
    assert.equal(url.pathname, "/login");
    assert.equal(url.searchParams.get("registered"), "1");
    assert.equal(url.searchParams.get("next"), "/community/members/21?tab=comments&page=2");
  }
});

test("unknown team codes are blocked before sending signup", async () => {
  const form = screen();
  form.fill("XX");
  await form.submit();
  assert.deepEqual(form.requests, []);
  assert.deepEqual(form.focused, ["#signup-team-code"]);
  assert.equal(form.render().find(node => node.props?.name === "teamCode").props["aria-invalid"], true);
});

test("server team errors are shown without navigating away", async (t) => {
  const focused = [];
  mockGlobal(t, "document", { getElementById: id => ({ focus: () => focused.push(id) }) });
  mockGlobal(t, "requestAnimationFrame", callback => callback());
  const form = screen({ failure: new ApiError("응원팀을 확인해 주세요.", 400, { team_code: ["응원팀을 확인해 주세요."] }) });
  form.fill("LG");
  await form.submit();
  assert.deepEqual(form.redirects, []);
  assert.deepEqual(focused, ["signup-team-code"]);
  assert.equal(form.render().find(node => node.props?.role === "alert").props.children, "응원팀을 확인해 주세요.");
});
