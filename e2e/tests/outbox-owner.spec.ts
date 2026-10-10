import { expect, test } from "../helpers/test";

import { apiLogin, createPlan, factsOfNode, jwtSubject } from "../helpers/api";
import { PHOTO } from "../helpers/env";
import { enterFact, outboxCount, phoneContext, shot, signIn, signInHere } from "../helpers/ui";

// Общий телефон: мастер отправляет запись и выходит, пока снимок ещё грузится (диалог «Удалить и
// выйти»); в той же вкладке входит другой человек. Оставшиеся шаги записи мастера (подтвердить
// снимки, отправить на проверку) не должны уйти ни под чьим токеном: отправка прервана на ходу,
// очередь стёрта (решение PO), а запросы записи в любом случае идут от имени её владельца.
test("выход с отправкой на ходу: запись прервана, чужой вход ничего не отправляет", async ({
  browser,
  request,
}) => {
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
      await route.continue().catch(() => undefined); // загрузка уже прервана выходом
    },
  );

  // Если сценарий упал раньше, ворота всё равно открываются: иначе закрытие контекста ждёт
  // зависший обработчик загрузки и вся прогонка встаёт.
  try {
    await enterFact(page, {
      nodeName: plan.nodeName,
      workName: plan.workName,
      volume: "15",
      photo: PHOTO,
    });
    await page.getByTestId("send").click();
    await held; // черновик факта создан, снимок уходит в S3 и стоит на воротах

    // Мастер выходит (запись ещё уходит: диалог), удаляет её и выходит; в той же вкладке (без
    // перезагрузки страницы) входит технический директор.
    await page.getByRole("button", { name: "Выйти", exact: true }).click();
    await page.getByTestId("logout-discard").click();
    await page.waitForURL(/\/login/);
    await signInHere(page, "techdir");
    await shot(page, "owner-1-other-user");

    open(); // снимок «дозагружается»: запись уже стёрта, продолжать нечему
    await page.waitForTimeout(4000);
    expect(await outboxCount(page), "очередь мастера осталась на устройстве").toBe(0);

    expect(
      steps.filter((item) => item.subject === stranger),
      "шаги чужой записи ушли с токеном другого человека",
    ).toEqual([]);
    const draft = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
    expect(draft, "факт создан один раз").toHaveLength(1);
    expect(draft[0].status).toBe("draft");
    expect(draft[0].photo_count ?? 0).toBe(0);

    // Подтверждений и отправки от имени мастера нет: запись прервана и стёрта.
    expect(steps.filter((item) => item.subject === owner)).toEqual([]);
  } finally {
    open();
    await phone.close();
  }
});
