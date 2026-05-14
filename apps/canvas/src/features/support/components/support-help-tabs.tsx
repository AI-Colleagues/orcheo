import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { Input } from "@/design-system/ui/input";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/design-system/ui/tabs";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/design-system/ui/accordion";
import { Textarea } from "@/design-system/ui/textarea";

interface FaqItem {
  id: string;
  question: string;
  answer: string;
}

const faqItems: FaqItem[] = [
  {
    id: "item-1",
    question: "What is Orcheo and how does it work?",
    answer:
      "Orcheo is the monitoring and management interface for Orcheo workflows. Use it to inspect execution history, manage credentials, and configure workflow settings. Workflows are authored via the SDK or AI coding agents.",
  },
  {
    id: "item-2",
    question: "How do I create my first workflow?",
    answer:
      'Create a new workflow from the dashboard, add a trigger node, attach action nodes, connect them together, then save and activate it when you are ready. The "New Workflow" button guides you through the process.',
  },
  {
    id: "item-3",
    question: "What types of integrations are available?",
    answer:
      "We support Google Workspace, Microsoft 365, Slack, Salesforce, HubSpot, HTTP requests, webhooks, databases, and any service with a public API.",
  },
  {
    id: "item-4",
    question: "How do I debug a workflow that's not working?",
    answer:
      "Use execution history, the workflow debugger, and node-level breakpoints to step through your automation and inspect the data flowing between nodes.",
  },
  {
    id: "item-5",
    question: "How do I manage team access and permissions?",
    answer:
      "Invite teammates from Settings → Teams and assign Owner, Admin, Editor, or Viewer roles to control who can view, edit, or manage workflows.",
  },
];

function FaqAccordion() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Frequently Asked Questions</CardTitle>
        <CardDescription>
          Find answers to common questions about Orcheo
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Accordion type="single" collapsible className="w-full">
          {faqItems.map((item) => (
            <AccordionItem key={item.id} value={item.id}>
              <AccordionTrigger>{item.question}</AccordionTrigger>
              <AccordionContent>{item.answer}</AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </CardContent>
      <CardFooter>
        <Button variant="outline" className="w-full">
          View All FAQs
        </Button>
      </CardFooter>
    </Card>
  );
}

function ContactSupportForm() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Contact Support</CardTitle>
        <CardDescription>Get help from our support team</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-6">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <label
                htmlFor="support-name"
                className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
              >
                Name
              </label>
              <Input id="support-name" placeholder="Enter your name" />
            </div>
            <div className="grid gap-2">
              <label
                htmlFor="support-email"
                className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
              >
                Email
              </label>
              <Input
                id="support-email"
                type="email"
                placeholder="Enter your email"
              />
            </div>
          </div>
          <div className="grid gap-2">
            <label
              htmlFor="support-subject"
              className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
            >
              Subject
            </label>
            <Input id="support-subject" placeholder="Enter subject" />
          </div>
          <div className="grid gap-2">
            <label
              htmlFor="support-message"
              className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
            >
              Message
            </label>
            <Textarea
              id="support-message"
              placeholder="Enter your message"
              className="min-h-[120px]"
            />
          </div>
        </div>
      </CardContent>
      <CardFooter className="flex justify-between">
        <Button variant="outline">Cancel</Button>
        <Button>Submit</Button>
      </CardFooter>
    </Card>
  );
}

export function SupportHelpTabs() {
  return (
    <Tabs defaultValue="faq" className="space-y-4">
      <TabsList>
        <TabsTrigger value="faq">Frequently Asked Questions</TabsTrigger>
        <TabsTrigger value="contact">Contact Support</TabsTrigger>
      </TabsList>
      <TabsContent value="faq" className="space-y-4">
        <FaqAccordion />
      </TabsContent>
      <TabsContent value="contact" className="space-y-4">
        <ContactSupportForm />
      </TabsContent>
    </Tabs>
  );
}
