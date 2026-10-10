/**
 * Example: Node.js / Express website backend notification helper.
 *
 * Call this ONLY from server-side code after the booking is successfully saved.
 * Never put WEBHOOK_SECRET in frontend / browser code.
 *
 * Env:
 *   BOOKING_BOT_WEBHOOK_URL=https://bot.example.com/api/webhooks/bookings
 *   BOOKING_BOT_WEBHOOK_SECRET=your_shared_secret
 */

async function notifyBookingCreated(booking) {
  const url = process.env.BOOKING_BOT_WEBHOOK_URL;
  const secret = process.env.BOOKING_BOT_WEBHOOK_SECRET;

  if (!url || !secret) {
    throw new Error("BOOKING_BOT_WEBHOOK_URL and BOOKING_BOT_WEBHOOK_SECRET are required");
  }

  // Prefer a stable event_id so website retries do not create duplicate Telegram messages.
  const eventId = `booking_created_${booking.id}`;

  const payload = {
    event_id: eventId,
    event_type: "booking.created",
    booking,
  };

  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 5000);
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${secret}`,
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`Webhook failed: ${response.status} ${text}`);
      }
      return response.json();
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 250 * 2 ** (attempt - 1)));
    }
  }
  throw lastError;
}

module.exports = { notifyBookingCreated };

// Manual smoke test: node examples/send_booking_webhook.js
if (require.main === module) {
  notifyBookingCreated({
    id: "12345",
    customer_name: "John Smith",
    customer_phone: "+998901234567",
    service_name: "Haircut",
    barber_name: "Aziz",
    date: "2026-10-15",
    time: "14:30",
    price: 100000,
    currency: "UZS",
    status: "confirmed",
  })
    .then((result) => {
      console.log(result);
    })
    .catch((error) => {
      console.error(error);
      process.exit(1);
    });
}
