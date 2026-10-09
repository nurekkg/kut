import { expect, test } from "../helpers/test";

import { apiLogin, createPlan, factsOfNode } from "../helpers/api";
import { PHOTO } from "../helpers/env";
import { enterFact, phoneContext, shot, signIn } from "../helpers/ui";

// Сценарий 4: обрыв связи между запросом и ответом. Запрос на создание факта доходит до сервера,
// ответ теряется, телефон теряет сеть и возвращается: запись уходит повторно с тем же ключом
// Idempotency-Key и в итоге на сервере ровно одна запись, а не две.
test("повторная отправка после обрыва не создаёт дубль", async ({ browser, request }) => {
  const foreman = await apiLogin(request, "foreman");
  const plan = await createPlan(request, foreman);

  const phone = await phoneContext(browser);
  const master = await phone.newPage();
  await signIn(master, "master");

  const keys: string[] = [];
  let dropped = false;
  await master.route("**/work-actuals", async (route) => {
    const call = route.request();
    if (call.method() !== "POST") return route.continue();
    keys.push(call.headers()["idempotency-key"] ?? "");
    if (dropped) return route.continue();
    dropped = true;
    await route.fetch(); // запрос дошёл до сервера, факт создан
    await route.abort("connectionreset"); // а ответ браузер не получил
    await phone.setOffline(true);
    setTimeout(() => void phone.setOffline(false), 1500);
  });

  await enterFact(master, {
    nodeName: plan.nodeName,
    workName: plan.workName,
    volume: "25",
    photo: PHOTO,
  });
  await master.getByTestId("send").click();
  await expect.poll(() => dropped, { timeout: 30_000 }).toBe(true);
  // Пока связи нет, запись лежит в очереди устройства и не потеряна.
  await expect(master.getByTestId("sent")).not.toHaveAttribute("data-state", "done");
  await shot(master, "retry-1-waiting");

  // Связь вернулась: очередь отправляется сама и доводит запись до проверки.
  await expect(master.getByTestId("sent")).toHaveAttribute("data-state", "done", {
    timeout: 120_000,
  });
  await shot(master, "retry-2-done");

  const facts = await factsOfNode(request, foreman, plan.projectId, plan.nodeId);
  expect(facts, "на сервере должна быть одна запись, а не две").toHaveLength(1);
  expect(facts[0].status).toBe("submitted");
  expect(facts[0].photo_count).toBe(1);
  // Оба запроса несли один и тот же ключ повтора.
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);

  await phone.close();
});
