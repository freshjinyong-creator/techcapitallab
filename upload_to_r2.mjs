import { S3Client, PutObjectCommand, ListObjectsV2Command } from "@aws-sdk/client-s3";
import fs from "fs";
import path from "path";
import crypto from "crypto";

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


function parseArgs(argv) {
  let srcDir = null;
  let manifestPath = null;
  const extraFiles = [];

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--src" && i + 1 < argv.length) {
      srcDir = path.resolve(argv[++i]);
    } else if (arg.startsWith("--src=")) {
      srcDir = path.resolve(arg.slice("--src=".length));
    } else if (arg === "--manifest" && i + 1 < argv.length) {
      manifestPath = path.resolve(argv[++i]);
    } else if (arg.startsWith("--manifest=")) {
      manifestPath = path.resolve(arg.slice("--manifest=".length));
    } else if (arg === "--snapshot" && i + 1 < argv.length) {
      extraFiles.push({
        filePath: path.resolve(argv[++i]),
        key: "data/quant_advisor_snapshot.json",
      });
    } else if (arg.startsWith("--snapshot=")) {
      extraFiles.push({
        filePath: path.resolve(arg.slice("--snapshot=".length)),
        key: "data/quant_advisor_snapshot.json",
      });
    } else if (arg === "--file" && i + 2 < argv.length) {
      const filePath = path.resolve(argv[++i]);
      const key = argv[++i].replace(/^\/+/, "");
      extraFiles.push({ filePath, key });
    } else {
      throw new Error(`Unknown or incomplete argument: ${arg}`);
    }
  }

  const shouldUploadSrc = srcDir !== null || extraFiles.length === 0;
  if (srcDir === null) {
    srcDir = path.join(process.cwd(), "public", "data");
  }

  return { srcDir, shouldUploadSrc, manifestPath, extraFiles };
}

function loadManifest(manifestPath) {
  if (!manifestPath || !fs.existsSync(manifestPath)) {
    return {};
  }
  try {
    const parsed = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch (err) {
    console.warn(`Warning: failed to parse manifest ${manifestPath} (${err.message}), starting fresh.`);
    return {};
  }
}

function saveManifestAtomic(manifestPath, manifest) {
  if (!manifestPath) return;
  const dir = path.dirname(manifestPath);
  fs.mkdirSync(dir, { recursive: true });
  const tmpPath = `${manifestPath}.${process.pid}.tmp`;
  fs.writeFileSync(tmpPath, JSON.stringify(manifest, null, 2) + "\n", "utf-8");
  fs.renameSync(tmpPath, manifestPath);
}

function sha256Hex(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

async function uploadBuffer(content, key) {
  const command = new PutObjectCommand({
    Bucket: BUCKET_NAME,
    Key: key,
    Body: content,
    ContentType: "application/json",
    CacheControl: "public, max-age=300",
  });
  await s3.send(command);
}

async function run() {
  const startMs = Date.now();
  const { srcDir, shouldUploadSrc, manifestPath, extraFiles } = parseArgs(process.argv.slice(2));
  const manifest = loadManifest(manifestPath);
  let manifestDirty = false;

  if (shouldUploadSrc) {
    console.log(`Source data directory: ${srcDir}`);
  }
  if (manifestPath) {
    console.log(`Manifest enabled: ${manifestPath} (${Object.keys(manifest).length} cached entries)`);
  }

  let uploadedCount = 0;
  let skippedCount = 0;
  let failedCount = 0;

  async function processSingleFile(filePath, key) {
    const content = fs.readFileSync(filePath);
    const hash = manifestPath ? sha256Hex(content) : null;
    if (manifestPath && manifest[key] === hash) {
      skippedCount++;
      return "skipped";
    }
    await uploadBuffer(content, key);
    if (manifestPath) {
      manifest[key] = hash;
      manifestDirty = true;
    }
    uploadedCount++;
    return "uploaded";
  }

  try {
    // 1. Upload explicit --snapshot / --file targets
    for (const item of extraFiles) {
      if (!fs.existsSync(item.filePath)) {
        failedCount++;
        console.error(`Failed: file not found ${item.filePath} for key ${item.key}`);
        continue;
      }
      try {
        const status = await processSingleFile(item.filePath, item.key);
        console.log(`${status === "skipped" ? "Skipped (unchanged)" : "Uploaded"} ${item.key}`);
      } catch (err) {
        failedCount++;
        console.error(`Failed uploading ${item.key}:`, err.message);
      }
    }

    if (shouldUploadSrc) {
      // 2. Upload stock_list.json
      const stockListPath = path.join(srcDir, "stock_list.json");
      if (fs.existsSync(stockListPath)) {
        try {
          const status = await processSingleFile(stockListPath, "data/stock_list.json");
          console.log(`${status === "skipped" ? "Skipped (unchanged)" : "Uploaded"} data/stock_list.json`);
        } catch (err) {
          failedCount++;
          console.error("Failed uploading stock_list.json:", err.message);
        }
      } else {
        console.warn(`Warning: ${stockListPath} not found, skipping.`);
      }

      // 3. Upload daily/*.json
      const dailyDir = path.join(srcDir, "daily");
      const files = fs.existsSync(dailyDir)
        ? fs.readdirSync(dailyDir).filter(f => f.endsWith(".json")).sort()
        : [];
      console.log(`Found ${files.length} daily JSON files to process.`);

      const CONCURRENCY = 40;
      let completed = 0;
      let index = 0;

      async function next() {
        if (index >= files.length) return;
        const file = files[index++];
        const filePath = path.join(dailyDir, file);
        const key = `data/daily/${file}`;

        try {
          await processSingleFile(filePath, key);
        } catch (err) {
          failedCount++;
          console.error(`Failed uploading ${file}:`, err.message);
        } finally {
          completed++;
          if (completed % 500 === 0 || completed === files.length) {
            const pct = ((completed / files.length) * 100).toFixed(1);
            console.log(`Progress: ${completed}/${files.length} (${pct}%) [uploaded=${uploadedCount}, skipped=${skippedCount}, failed=${failedCount}]`);
          }
        }
      }

      const workers = Array.from({ length: CONCURRENCY }, async () => {
        while (index < files.length) {
          await next();
        }
      });

      await Promise.all(workers);
    }
  } finally {
    if (manifestPath && manifestDirty) {
      saveManifestAtomic(manifestPath, manifest);
      console.log(`Saved manifest to ${manifestPath} (${Object.keys(manifest).length} entries).`);
    }
  }

  const durationSec = ((Date.now() - startMs) / 1000).toFixed(2);
  console.log(`Upload finished in ${durationSec}s: uploaded=${uploadedCount}, skipped=${skippedCount}, failed=${failedCount}`);

  if (failedCount > 0) {
    console.error(`Exiting non-zero due to ${failedCount} failed uploads.`);
    process.exit(1);
  }
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
