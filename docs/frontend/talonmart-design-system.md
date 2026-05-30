# TalonMart Design System

This document defines the initial frontend design system for TalonMart, a desktop-first consumer retail storefront backed by the existing warehouse, procurement, order, and delivery APIs.

## Product Positioning

TalonMart is a consumer-facing general retail storefront. It should feel like a credible large-scale ecommerce site with dense product discovery, clear search, visible promotions, and a practical shopping flow.

The interface can reference the information structure of Walmart-style retail sites, but it must keep an independent brand identity. Do not use Walmart logos, trademarks, copy, or product imagery as first-party assets.

The frontend should focus on:

- Product browsing and search.
- Category-based discovery.
- Promotional product areas.
- Cart management.
- Order creation through the existing backend.
- Order status visibility.
- Lightweight inventory and fulfillment messaging that consumers can understand.

The frontend should not expose internal warehouse details such as batch numbers, storage locations, expiry risk, procurement handoff states, or replenishment workflows to consumers.

## Technical Boundary

The frontend stack is:

- Vue 3.
- Vite.
- TypeScript.
- Vue Router.
- Pinia.
- Axios for HTTP requests.
- Tailwind CSS for styling.
- lucide-vue-next for icons.

Nuxt 3 and SSR are out of scope for the first version. The existing backend remains the source of business logic and data. The frontend is a separate client application that calls backend APIs.

Authentication is out of scope for the first version. The storefront should use a fixed mock customer when creating orders.

Desktop web is the primary target. Mobile should receive basic responsive fallback only: no horizontal overflow, usable header wrapping, and readable content. A dedicated mobile app-style navigation model is out of scope.

## Brand Personality

TalonMart should feel:

- Clear.
- Trustworthy.
- Fast.
- Practical.
- Retail-oriented.
- Slightly technical, but not like a dashboard.

The visual direction is a clean, credible, dense large-retail interface. It should not feel like a marketing landing page, luxury boutique, or backend admin system.

## Color System

Use a deep navy and cyan palette.

```txt
Primary Navy:        #0F2A44
Primary Navy Hover:  #123A5D
Accent Cyan:         #00A6C8
Accent Cyan Soft:    #E6F8FB
Surface:             #FFFFFF
Page Background:     #F5F7FA
Border:              #D8E0E8
Text Strong:         #101828
Text Muted:          #667085
Promo Amber:         #FFB020
Discount Red:        #D92D20
Success Green:       #039855
```

Color usage rules:

- Navy is used for the global header, primary buttons, and high-emphasis navigation.
- Cyan is used for fulfillment confidence, selected states, secondary emphasis, and subtle highlights.
- Amber is reserved for promotions, deals, and campaign modules.
- Red is reserved for discounts, destructive states, and errors.
- Green is reserved for successful states and positive stock availability.
- Do not rely on color alone to communicate state. Pair color with text or icons.

## Typography

Use the system font stack. Do not load external web fonts in the first version.

```css
font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
```

Type rules:

- Body text should default to `16px`.
- Secondary text can use `14px`.
- Avoid body text below `14px`.
- Product titles should wrap to a maximum of two lines in product cards.
- Prices should use stronger weight and tabular numerals where practical.
- Letter spacing should stay at the default value.

Recommended scale:

```txt
12px: minor metadata
14px: secondary labels, helper text
16px: default body and controls
18px: compact section titles
24px: page section titles
32px: homepage campaign headings
```

## Layout System

The primary desktop content width is approximately `1440px`.

Core layout rules:

- Use a centered content container for main page content.
- Keep the ecommerce density high, but preserve enough spacing for scanability.
- Use `12px` to `16px` gaps between dense product cards.
- Use `24px` to `32px` vertical rhythm between major homepage sections.
- Keep card radius at `8px` or less.
- Do not nest cards inside cards.
- Use fixed image aspect ratios to prevent layout shift.

The homepage first viewport should prioritize:

1. Global header.
2. Search.
3. Category entry points.
4. Promotion or deal area.
5. Product recommendations.

## Global Header

The desktop header should include:

- TalonMart brand mark or wordmark.
- Delivery or location affordance.
- Large central search input.
- Account placeholder, even if login is not implemented.
- Orders link.
- Cart link with item count.

The second navigation row should include:

- Main product categories.
- Service or deal links where needed.

The header should be visually dominant but not oversized. Search is the highest-priority interaction.

## Category Scope

The first version should stay aligned with the current backend sample product universe.

Initial categories:

- Paper Goods.
- Dairy.
- Beverages.
- Office Supplies.

Avoid adding broad marketplace categories unless backend data is also expanded to support them.

## Core Components

The first version should define and reuse these components:

- `AppHeader`
- `SearchBar`
- `CategoryNav`
- `PromoBanner`
- `ProductCard`
- `PriceBlock`
- `StockBadge`
- `AddToCartButton`
- `CartPage`
- `OrderSummary`
- `OrderStatusBadge`
- `EmptyState`
- `SkeletonLoader`

Component rules:

- Primary buttons should be at least `40px` tall.
- Interactive hit areas should be at least `44px` where practical.
- Icon-only buttons require accessible labels.
- Buttons need visible hover, active, disabled, and focus states.
- Loading buttons should disable repeat submission.
- Use lucide icons instead of emoji for functional UI.

## Product Card

A product card should show:

- Product image.
- Category or brand.
- Product name.
- Rating placeholder if backend rating data is not available.
- Current price.
- Original price and discount label when applicable.
- Lightweight stock or fulfillment status.
- Add to cart button.

The product card should not show internal fields such as:

- Batch number.
- Storage location.
- Expiry risk.
- Reorder threshold.
- Procurement status.

Consumer-facing inventory language:

```txt
In stock
Low stock
Unavailable
Delivery blocked
```

If a backend fulfillment check returns blockers, convert them into concise user-facing copy.

## Promotion Model

The storefront should use a promotion-oriented ecommerce model.

Supported display concepts:

- Current price.
- Original price.
- Discount badge.
- Today deals section.
- Campaign banner.
- Cart subtotal savings.

Membership pricing, coupons, and loyalty features are out of scope for the first version.

## Cart And Orders

The cart should support:

- Add item.
- Increase quantity.
- Decrease quantity.
- Remove item.
- Show subtotal.
- Show estimated discount savings where data exists.
- Submit order.

Order submission should call the existing backend order creation endpoint. The backend owns inventory deduction and order status transitions.

The order UI should show:

- Order ID.
- Items.
- Status.
- Shipping or delivery metadata returned by the backend.
- Lightweight status messaging.

Payment integration is out of scope. The first version can present order creation as a checkout confirmation flow.

## Images

Product images can use remote public image URLs for the first version.

Image rules:

- Use fixed aspect-ratio containers.
- Use `object-fit: cover` or `object-fit: contain` according to product type.
- Reserve layout space before images load.
- Provide alt text from product names.
- Provide a consistent fallback image state for failed image loads.

## State And Feedback

Every async data area should include:

- Loading state.
- Empty state.
- Error state.
- Retry affordance where useful.

Use skeletons for product grids and page-level loading. Use inline errors for local failures and toast-style feedback for cart or order actions.

## Accessibility

Baseline requirements:

- Text contrast should meet WCAG AA where practical.
- Keyboard focus must be visible.
- Header navigation and search must be keyboard usable.
- Form fields need visible labels or accessible labels.
- Image alt text is required for meaningful product images.
- Do not remove browser zoom.
- Do not communicate stock, discount, or error state by color alone.

## Out Of Scope For Version 1

- Nuxt 3.
- SSR.
- Full mobile navigation design.
- User registration or login.
- Real payment integration.
- Membership pricing.
- Internal warehouse dashboards.
- Procurement operations UI.
- Batch, location, expiry, or replenishment details in consumer UI.
