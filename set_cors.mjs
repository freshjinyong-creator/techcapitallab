import { S3Client, PutBucketCorsCommand } from "@aws-sdk/client-s3";

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

async function setCors() {
  const corsRule = {
    CORSRules: [
      {
        AllowedHeaders: ["*"],
        AllowedMethods: ["GET", "HEAD"],
        AllowedOrigins: ["*"],
        MaxAgeSeconds: 86400,
      },
    ],
  };

  const command = new PutBucketCorsCommand({
    Bucket: BUCKET_NAME,
    CORSConfiguration: corsRule,
  });

  await s3.send(command);
  console.log("CORS configured successfully on R2 bucket!");
}

setCors().catch(console.error);
