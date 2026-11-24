# AddisVerify onboarding: identity verification (v1.4)

## Goal
New customers verify their identity online before opening an account. Most
customers are in Ethiopia; many have names written in Amharic (Ge'ez script).

## Requirements
1. Customers can verify with a **National ID, a Passport or a Driver's licence**.
   All three are supported equally.
2. The customer's **full name is entered as written on the document**, in
   Latin or Ge'ez script.
3. **Expired documents must be rejected** (National Bank of Ethiopia KYC
   directive). A document is expired if its expiry date is before today.
4. The phone number is optional at this stage.
5. The customer must accept the terms before verification.
6. After verification, **My documents** lists every verified document, with
   the total count.
7. After verification, the customer sees **their result**: the verification
   status and the document's details as recorded.
8. **Signing out removes the customer's session and details** from the device.
   Many customers verify on a shared phone at an agent's shop.

## Out of scope for v1.4
Selfie and liveness checks; document photo upload.
