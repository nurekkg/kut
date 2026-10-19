import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// Системный переход версии, как у сида и тестов: переход делает воркер через функцию БД. Маршрута
// утверждения в API пока нет (S6a), поэтому фикстура переводит версию через базу dev-стенда.
export async function transitionVersion(versionId: string, to: "on_approval" | "approved"): Promise<void> {
  if (!UUID.test(versionId)) throw new Error(`не идентификатор версии: ${versionId}`);
  const sql = `SELECT app.version_move(CAST('${versionId}' AS uuid), '${to}', NULL, NULL)`;
  await run("docker", [
    "exec",
    "kut-postgres-1",
    "psql",
    "-U",
    "kut_worker",
    "-d",
    "kut",
    "-v",
    "ON_ERROR_STOP=1",
    "-c",
    sql,
  ]);
}
