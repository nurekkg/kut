import { expect, test } from "../helpers/test";
import { apiLogin, get } from "../helpers/api";
import { transitionVersion } from "../helpers/db";
import { createDocument, uploadVersion } from "../helpers/documents";
import { EAST_NAME, projectNamed } from "../helpers/equipment";
import { zipStored } from "../helpers/zip";
import {
  desktopContext,
  expectNoHorizontalScroll,
  phoneContext,
  shot,
  signIn,
  watchErrors,
} from "../helpers/ui";

// S6a-1 (шаги 0-2): ПТО импортирует позиции КС-2 из XLSX и видит цены и итог. Прораб видит объёмы, а цен в
// DOM нет. Заказчик видит позиции и цены утверждённой версии. Снимки экранов - 375 и 1280 px.

const PDF = Buffer.from("%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n");
const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const CUSTOMER = "Заказчик (dev)";
const HEADER = ["№", "Наименование", "Ед.", "Количество", "Цена"];

// Минимальная книга: один лист «Позиции»; строки - inlineStr, числа - значением.
function xlsxBytes(rows: (string | number)[][]): Buffer {
  const cell = (value: string | number, row: number, column: number) => {
    const ref = `${String.fromCharCode(65 + column)}${row}`;
    return typeof value === "number"
      ? `<c r="${ref}"><v>${value}</v></c>`
      : `<c r="${ref}" t="inlineStr"><is><t>${value}</t></is></c>`;
  };
  const sheet = rows
    .map(
      (values, index) =>
        `<row r="${index + 1}">${values.map((value, column) => cell(value, index + 1, column)).join("")}</row>`,
    )
    .join("");
  const xml = (body: string) => Buffer.from(`<?xml version="1.0" encoding="UTF-8"?>${body}`);
  return zipStored({
    "[Content_Types].xml": xml(
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
        '<Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
        "</Types>",
    ),
    "_rels/.rels": xml(
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
        "</Relationships>",
    ),
    "xl/workbook.xml": xml(
      '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' +
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
        '<sheets><sheet name="Позиции" sheetId="1" r:id="rId1"/></sheets></workbook>',
    ),
    "xl/_rels/workbook.xml.rels": xml(
      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
        "</Relationships>",
    ),
    "xl/worksheets/sheet1.xml": xml(
      `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${sheet}</sheetData></worksheet>`,
    ),
  });
}

const BOOK = xlsxBytes([
  HEADER,
  [1, "Бетон B25", "м3", 2, 100],
  [2, "Арматура", "т", 1.5, 50.5],
]);

test("позиции версии: ПТО импортирует и видит цены, прораб видит объёмы без цен, заказчик - утверждённое", async ({
  browser,
  request,
}) => {
  // Шесть контекстов входа и загрузки карточки подряд: по минутам на каждый, поэтому бюджет шире обычного.
  test.setTimeout(900_000);
  const techdir = await apiLogin(request, "techdir");
  const estimator = await apiLogin(request, "estimator");
  const east = await projectNamed(request, techdir, EAST_NAME);
  const counterparties = await get<{ id: string; name: string }[]>(
    request,
    techdir,
    "/api/v1/documents/counterparties",
  );
  const customer = counterparties.find((item) => item.name === CUSTOMER);
  expect(customer, "контрагент заказчика есть в справочнике").toBeDefined();
  const document = await createDocument(request, techdir, {
    kind: "ks2",
    legal_entity_id: east.legal_entity_id,
    project_id: east.id,
    counterparty_id: customer!.id,
    title: `E2E КС-2 с позициями ${Date.now()}`,
  });
  const version = await uploadVersion(request, estimator, document.id, PDF, "application/pdf");

  // ПТО: импорт в черновик, таблица и итог с ценами.
  const desktop = await desktopContext(browser);
  const estimatorPage = await desktop.newPage();
  const estimatorErrors = watchErrors(estimatorPage);
  await signIn(estimatorPage, "estimator");
  await estimatorPage.goto(`/documents/${document.id}`);
  await expect(estimatorPage.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await estimatorPage.getByTestId("document-version-lines-toggle").click();
  await expect(estimatorPage.getByTestId("document-lines")).toBeVisible();
  await estimatorPage.getByTestId("document-lines-file").setInputFiles({
    name: "pozicii.xlsx",
    mimeType: XLSX,
    buffer: BOOK,
  });
  await expect(estimatorPage.getByTestId("document-lines-table")).toContainText("Бетон B25", {
    timeout: 40_000,
  });
  await expect(estimatorPage.getByTestId("document-lines-total")).toContainText("275,75");
  await shot(estimatorPage, "lines-estimator-1280");
  expect(estimatorErrors).toEqual([]);

  // ПТО на телефоне: карточки позиций с ценами, без горизонтальной прокрутки.
  const phoneEstimator = await (await phoneContext(browser)).newPage();
  const phoneEstimatorErrors = watchErrors(phoneEstimator);
  await signIn(phoneEstimator, "estimator");
  await phoneEstimator.goto(`/documents/${document.id}`);
  await expect(phoneEstimator.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await phoneEstimator.getByTestId("document-version-lines-toggle").click();
  await expect(phoneEstimator.getByTestId("document-line-card").first()).toContainText("Бетон B25");
  await expectNoHorizontalScroll(phoneEstimator);
  await shot(phoneEstimator, "lines-estimator-375");
  expect(phoneEstimatorErrors).toEqual([]);

  // Прораб: объёмы видны, цен и итога в DOM нет, писать нельзя.
  const foremanPage = await (await desktopContext(browser)).newPage();
  const foremanErrors = watchErrors(foremanPage);
  await signIn(foremanPage, "foreman");
  await foremanPage.goto(`/documents/${document.id}`);
  await expect(foremanPage.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await foremanPage.getByTestId("document-version-lines-toggle").click();
  const foremanTable = foremanPage.getByTestId("document-lines-table");
  await expect(foremanTable).toContainText("Бетон B25");
  await expect(foremanTable).toContainText("Арматура");
  await expect(foremanTable.getByRole("columnheader")).toHaveCount(4);
  await expect(foremanPage.getByTestId("document-lines-total")).toHaveCount(0);
  await expect(foremanPage.getByTestId("document-lines")).not.toContainText("сом");
  await expect(foremanPage.getByTestId("document-lines-edit")).toHaveCount(0);
  await expect(foremanPage.getByTestId("document-lines-import")).toHaveCount(0);
  await shot(foremanPage, "lines-foreman-1280");
  expect(foremanErrors).toEqual([]);

  const phoneForeman = await (await phoneContext(browser)).newPage();
  const phoneForemanErrors = watchErrors(phoneForeman);
  await signIn(phoneForeman, "foreman");
  await phoneForeman.goto(`/documents/${document.id}`);
  await expect(phoneForeman.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await phoneForeman.getByTestId("document-version-lines-toggle").click();
  await expect(phoneForeman.getByTestId("document-line-card").first()).toContainText("Бетон B25");
  await expect(phoneForeman.getByTestId("document-lines")).not.toContainText("сом");
  await expectNoHorizontalScroll(phoneForeman);
  await shot(phoneForeman, "lines-foreman-375");
  expect(phoneForemanErrors).toEqual([]);

  // Заказчик: черновик ему не виден; после утверждения - позиции и цены, без правок.
  await transitionVersion(version.id, "on_approval");
  await transitionVersion(version.id, "approved");
  const customerPage = await (await desktopContext(browser)).newPage();
  const customerErrors = watchErrors(customerPage);
  await signIn(customerPage, "customer");
  await customerPage.goto(`/documents/${document.id}`);
  await expect(customerPage.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await customerPage.getByTestId("document-version-lines-toggle").click();
  await expect(customerPage.getByTestId("document-lines-table")).toContainText("Арматура");
  await expect(customerPage.getByTestId("document-lines-total")).toContainText("275,75");
  await expect(customerPage.getByTestId("document-lines-edit")).toHaveCount(0);
  await expect(customerPage.getByTestId("document-lines-import")).toHaveCount(0);
  await shot(customerPage, "lines-customer-1280");
  expect(customerErrors).toEqual([]);

  const phoneCustomer = await (await phoneContext(browser)).newPage();
  const phoneCustomerErrors = watchErrors(phoneCustomer);
  await signIn(phoneCustomer, "customer");
  await phoneCustomer.goto(`/documents/${document.id}`);
  await expect(phoneCustomer.getByTestId("document-card")).toBeVisible({ timeout: 40_000 });
  await phoneCustomer.getByTestId("document-version-lines-toggle").click();
  await expect(phoneCustomer.getByTestId("document-line-card").first()).toContainText("Бетон B25");
  await expectNoHorizontalScroll(phoneCustomer);
  await shot(phoneCustomer, "lines-customer-375");
  expect(phoneCustomerErrors).toEqual([]);
});
