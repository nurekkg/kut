import { expect, test } from "../helpers/test";

import { apiLogin, createPlan, factsOfNode, jwtSubject } from "../helpers/api";
import { PHOTO } from "../helpers/env";
import { enterFact, phoneContext, shot, signIn, signInHere } from "../helpers/ui";

// Общий телефон: мастер отправляет запись и выходит, пока снимок ещё грузится; в той же вкладке
// входит другой человек. Оставшиеся шаги записи мастера (подтвердить снимки, отправить на проверку)
// не должны уйти с токеном второго: иначе снимки и отправка записались бы от его имени, а авторство
// доказательств исказилось бы. Запись мастера остаётся в очереди устройства и доходит, когда он
// возвращается.
test("запись очереди не уходит под сессией другого человека", async ({ browser, request }) => {
  const foreman = await apiLogin(request, "foreman");
  const plan = await createPlan(request, foreman);
  const masterToken = (await apiLogin(request, "master")).token;
  const techdirToken = (await apiLogin(request, "techdir")).token;
  const owner = jwtSubject(masterToken);
  const stranger = jwtSubject(techdirToken);
  expect(owner).not.toBe(stranger);

  const phone = await phoneContext(browser);
  const page = await phone.newPage();
  await signIn(page, "master");

  // Шаги записи после загрузки снимка и то, чьим токеном они ушли.
  const steps: { step: "confirm" | "submit"; subject: string }[] = [];
  page.on("request", (call) => {
    if (call.method() !== "POST") return;
    const path = new URL(call.url()).pathname;
    const step = path.endsWith("/photos/confirm")
      ? "confirm"
      : /\/work-actuals\/[^/]+\/submit$/.test(path)
        ? "submit"
        : null;
    if (!step) return;
    const bearer = (call.headers()["authorization"] ?? "").replace(/^Bearer /, "");
    steps.push({ step, subject: bearer ? jwtSubject(bearer) : "" });
  });

  // Загрузку снимка в S3 держим, пока человека на устройстве не сменят.
  let open: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    open = resolve;
  });
  let reached: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    reached = resolve;
  });
  await page.route(
    (url) => url.port === "9000",
    async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      reached();
      await gate;
      await route.continue();
    },
  );

  await enterFact(page, {
    nodeName: plan.nodeName,
    workName: plan.workName,
    volume: "15",
    photo: PHOTO,
  });
  await page.getByTestId("send").click();
  await held; // черновик факта создан, снимок уходит в S3 и стоит на воротах

  // Мастер выходит, в той же вкладке (без перезагрузки страницы) входит технический директор.
  await page.getByRole("button", { name: "Выйти" }).click();
  await page.waitForURL(/\/login/);
  await signInHere(page, "techdir");
  await shot(page, "owner-1-other-user");

  // Снимок дозагружен; все шаги, которые запись успела бы сделать, уже сделаны.
  const stored = page.waitForResponse(
    (response) => response.request().method() === "PUT" && new URL(response.url()).port === "9000",
  );
  open();
  await stored;
  await page.waitForTimeout(4000);

  expect(
    steps.filter((item) => item.subject === stranger),
    "шаги чужой записи ушли с токеном другого человека",
  ).toEqual([]);
  const draft = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
  expect(draft, "факт создан один раз").toHaveLength(1);
  expect(draft[0].status).toBe("draft");
  expect(draft[0].photo_count ?? 0).toBe(0);

  // Мастер возвращается: запись цела и доводится до проверки уже под его токеном.
  await phone.clearCookies();
  await signIn(page, "master");
  await page.goto("/m");
  await expect
    .poll(
      async () => {
        const facts = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
        return `${facts.length}:${facts[0]?.status}:${facts[0]?.photo_count}`;
      },
      { timeout: 120_000, intervals: [2000] },
    )
    .toBe("1:submitted:1");
  // Повтор шага при сбое сервера допустим, поэтому порядок и состав, а не точное число запросов.
  const mine = steps.filter((item) => item.subject === owner).map((item) => item.step);
  expect(mine[0]).toBe("confirm");
  expect(mine).toContain("submit");
  await shot(page, "owner-2-owner-back");
  await phone.close();
});
