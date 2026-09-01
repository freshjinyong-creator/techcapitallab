import { S3Client, PutObjectCommand, ListObjectsV2Command } from "@aws-sdk/client-s3";
import fs from "fs";
import path from "path";

const ACCOUNT_ID = "c18c83d11d4a9441569e65fb5b50812d";
const ACCESS_KEY_ID = "c3dd33cc9b9542d97168de43936efe1b";
const SECRET_ACCESS_KEY = "ea5b06107c8de085d65abdb73e6baa087df91ae77976b55c26a2d9e3dbff62f0";
const BUCKET_NAME = "quant-data";

const s3 = new S3Client({
  region: "auto",
  endpoint: `https://${ACCOUNT_ID}.r2.cloudflarestorage.com`,
  credentials: {
    accessKeyId: ACCESS_KEY_ID,
    secretAccessKey: SECRET_ACCESS_KEY,
  },
});

async function uploadFile(filePath, key) {
  const content = fs.readFileSync(filePath);
  const command = new PutObjectCommand({
    Bucket: BUCKET_NAME,
    Key: key,
    Body: content,
    ContentType: "application/json",
  });
  await s3.send(command);
}

async function run() {
  console.log("Testing connection to R2...");
  const testKey = "test.json";
  fs.writeFileSync("/tmp/test.json", JSON.stringify({ status: "ok" }));
  await uploadFile("/tmp/test.json", testKey);
  console.log("Test file uploaded successfully!");

  const dataDir = "/home/freshjinyong/techcapitallab/public/data";
  
  // 1. Upload stock_list.json
  const stockListPath = path.join(dataDir, "stock_list.json");
  if (fs.existsSync(stockListPath)) {
    console.log("Uploading stock_list.json...");
    await uploadFile(stockListPath, "data/stock_list.json");
    console.log("Uploaded data/stock_list.json");
  }

  // 2. Upload daily/*.json
  const dailyDir = path.join(dataDir, "daily");
  const files = fs.readdirSync(dailyDir).filter(f => f.endsWith(".json"));
  console.log(`Found ${files.length} daily JSON files to upload.`);

  const CONCURRENCY = 40;
  let completed = 0;
  let active = 0;
  let index = 0;

  async function next() {
    if (index >= files.length) return;
    const file = files[index++];
    const filePath = path.join(dailyDir, file);
    const key = `data/daily/${file}`;

    try {
      await uploadFile(filePath, key);
      completed++;
      if (completed % 200 === 0 || completed === files.length) {
        const pct = ((completed / files.length) * 100).toFixed(1);
        console.log(`Progress: ${completed}/${files.length} (${pct}%)`);
      }
    } catch (err) {
      console.error(`Failed uploading ${file}:`, err.message);
    }
  }

  const workers = Array.from({ length: CONCURRENCY }, async () => {
    while (index < files.length) {
      await next();
    }
  });

  await Promise.all(workers);
  console.log("All files uploaded successfully to R2!");
}

run().catch(console.error);
